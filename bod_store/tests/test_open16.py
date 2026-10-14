"""OPEN-16 (review sim findings) + e-stop release hardening. SIM ONLY."""
import ast, sqlite3, time
from pathlib import Path
import pytest
from rag_adapter import body_status, body_status_block, connect_ro
from triggers import detect
from store import SimStore
import estop_release as op_mod

HOME = Path(__file__).resolve().parents[1]

def _ev(trigs): return {t["trigger_event"]: t for t in trigs}
def _goal(now=1000, pid=None, env=None):
    return _ev(detect(now=now, goal={"pose_id": pid or env["recs"][1]["pose_id"]}))["goal_requested"]

@pytest.fixture
def ready(env):
    env["st"].rehome(env["recs"][0]["pose_id"])
    return env

# ---------- 1. e-stop latch (OPEN-16a) ----------
def test_rehome_does_not_clear_estop(ready):
    st, r = ready["st"], ready["recs"]
    st.set_estop(True)
    assert body_status(now=1000)["estop"]
    st.rehome(r[0]["pose_id"])
    s = body_status(now=1000)
    assert s["estop"] and not s["motion_allowed"]
    assert "estop_active" in _ev(detect(now=1000))

def test_update_from_sim_cannot_clear_estop(ready):
    st, r = ready["st"], ready["recs"]
    st.set_estop(True)
    # rest record has health.estop=released — must not clear latch
    assert r[0].get("health", {}).get("estop") == "released"
    st.update_from_sim(r[0])
    assert body_status(now=1000)["estop"]

def test_record_may_raise_estop_but_not_clear(env):
    st, r = env["st"], env["recs"]
    rec = __import__("copy").deepcopy(r[0])
    rec["health"] = dict(rec.get("health") or {}, estop="active")
    # remint so pose_id matches (update_from_sim checks §2.8)
    import bod_ids
    rec = bod_ids.remint(rec)
    st.update_from_sim(rec)
    assert body_status(now=1000)["estop"]
    st.update_from_sim(r[0])  # released in record
    assert body_status(now=1000)["estop"]  # still latched

def test_motion_allowed_false_while_estop(ready):
    st = ready["st"]
    st.set_estop(True)
    st.set_motion_allowed(True)  # must not stick
    assert not body_status(now=1000)["motion_allowed"]

def test_set_estop_false_refused(ready):
    ready["st"].set_estop(True)
    with pytest.raises(ValueError, match="estop_release.release_estop"):
        ready["st"].set_estop(False)
    assert body_status(now=1000)["estop"]

def test_release_estop_requires_id_and_reason(ready):
    ready["st"].set_estop(True)
    with pytest.raises(ValueError, match="operator_id"):
        op_mod.release_estop("", "checked")
    with pytest.raises(ValueError, match="reason"):
        op_mod.release_estop("mike", "")
    assert body_status(now=1000)["estop"]

def test_release_estop_writes_audit_and_clears(ready):
    ready["st"].set_estop(True)
    before = time.time()
    info = op_mod.release_estop("mike", "visual clear, SW1 reset")
    after = time.time()
    assert not body_status(now=1000)["estop"]
    con = sqlite3.connect(ready["db"])
    rows = con.execute("SELECT ts, when_cdt, operator_id, reason FROM estop_audit").fetchall()
    assert len(rows) == 1
    ts, when_cdt, oid, reason = rows[0]
    assert oid == "mike" and reason == "visual clear, SW1 reset"
    assert before <= ts <= after + 1
    assert "CDT" in when_cdt or "CST" in when_cdt
    # append-only
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("DELETE FROM estop_audit")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("UPDATE estop_audit SET reason='x'")
    # events row too
    kinds = [r[0] for r in con.execute("SELECT kind FROM events WHERE kind='estop_released'")]
    assert kinds == ["estop_released"]

def test_adapter_readonly_cannot_change_estop(ready):
    ready["st"].set_estop(True)
    con = connect_ro(ready["db"])
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT OR REPLACE INTO meta VALUES ('estop', 'released')")
    assert body_status(now=1000)["estop"]

def test_adapter_and_triggers_do_not_import_release():
    """AST: rag_adapter / triggers must not import operator or reference release_estop/set_estop."""
    forbidden_mods = {"estop_release", "operator"}
    forbidden_names = {"release_estop", "set_estop"}
    for fname in ("rag_adapter.py", "triggers.py"):
        src = (HOME / fname).read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert a.name.split(".")[0] not in forbidden_mods, fname
            if isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[0]
                assert mod not in forbidden_mods, fname
                for a in node.names:
                    assert a.name not in forbidden_names, (fname, a.name)
            if isinstance(node, ast.Name):
                assert node.id not in forbidden_names, (fname, node.id)
            if isinstance(node, ast.Attribute):
                assert node.attr not in forbidden_names, (fname, node.attr)

def test_event_log_uses_real_clock(ready):
    """Event header ts comes from real wall clock, not the sim clock (sim is ~1000)."""
    t0 = time.time()
    ready["st"].switch_off("head.jaw")
    ts = ready["st"].con.execute(
        "SELECT ts FROM events WHERE kind='switch_off' ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert ts >= t0 - 1 and abs(ts - 1000) > 1000  # not the fake sim clock

# ---------- 2. rehome keeps off (OPEN-16 / design review) ----------
def test_rehome_keeps_off_joints(ready):
    st, r = ready["st"], ready["recs"]
    st.switch_off("leftHand.pinky")
    assert body_status(now=1000)["off"] == ["leftHand.pinky"]
    st.rehome(r[0]["pose_id"])
    s = body_status(now=1000)
    assert s["off"] == ["leftHand.pinky"]
    assert "leftHand.pinky" not in [x.split(":")[0] for x in s["fault"]]
    row = st.con.execute("SELECT status, angle_deg, confidence, energized FROM current_state WHERE joint='leftHand.pinky'").fetchone()
    assert row == ("off", None, 0.0, 0)
    # pose record keeps digit 5
    rec = st.current_record()
    # find pinky svo digit
    assert any(j["name"] == "leftHand.pinky" and j["status"] == "offline" for j in rec["joint_vector"])

def test_switch_on_returns_unknown_until_rehome(ready):
    st, r = ready["st"], ready["recs"]
    st.switch_off("head.jaw")
    st.rehome(r[0]["pose_id"])
    assert body_status(now=1000)["off"] == ["head.jaw"]
    st.switch_on("head.jaw")
    s = body_status(now=1000)
    assert s["off"] == [] and "head.jaw" in s["unknown"]
    assert "joint_unknown" in _ev(detect(now=1000))
    st.rehome(r[0]["pose_id"])
    s = body_status(now=1000)
    assert "head.jaw" not in s["unknown"] and "head.jaw" not in s["off"]
    assert st.con.execute("SELECT status FROM current_state WHERE joint='head.jaw'").fetchone()[0] == "ok"

def test_switch_on_only_when_off(ready):
    with pytest.raises(ValueError, match="not off"):
        ready["st"].switch_on("head.jaw")

# ---------- 3. goal record faults (OPEN-16c) ----------
def test_goal_refuses_E5_faulted_in_record(ready):
    g = _goal(pid=ready["recs"][4]["pose_id"], env=ready)  # rest+LB_stuck45
    assert g["safe_action"] == "REFUSE_MOVE"
    assert any("leftArm.bicep" in x and "faulted" in x for x in g["failed_checks"])

def test_goal_refuses_E6_faulted_in_record(ready):
    g = _goal(pid=ready["recs"][5]["pose_id"], env=ready)  # rest+topStom_B_dead
    assert g["safe_action"] == "REFUSE_MOVE"
    assert any("torso.topStom" in x and "faulted" in x for x in g["failed_checks"])

def test_goal_refuses_E3_offline_in_record(ready):
    g = _goal(pid=ready["recs"][2]["pose_id"], env=ready)
    assert g["safe_action"] == "REFUSE_MOVE"
    assert any("offline" in x for x in g["failed_checks"])

# ---------- 4. gravity switch_off -> unknown (OPEN-16b) ----------
def test_switch_off_gravity_becomes_unknown_and_triggers(ready):
    st = ready["st"]
    st.switch_off("rightArm.shoulder")
    s = body_status(now=1000)
    assert "rightArm.shoulder" in s["unknown"]
    assert "rightArm.shoulder" not in s["off"]
    assert "joint_unknown" in _ev(detect(now=1000))
    assert st.con.execute("SELECT COUNT(*) FROM faults").fetchone()[0] == 0

def test_switch_off_nongravity_stays_off(ready):
    ready["st"].switch_off("leftHand.pinky")
    s = body_status(now=1000)
    assert s["off"] == ["leftHand.pinky"] and "leftHand.pinky" not in s["unknown"]

# ---------- 5. stale message + scope_limbs (OPEN-16d) ----------
def test_stale_reason_never_verified(env):
    env["st"].update_from_sim(env["recs"][0])  # no rehome → never verified
    t = _ev(detect(now=1000, stale_after_s=5))["state_stale"]
    assert t["reason"] == "never verified"
    assert "Nones" not in t["reason"]

def test_scope_limbs_empty_for_joint_scope(ready):
    ready["st"].raise_fault("head.jaw", "weak")
    t = _ev(detect(now=1000))["fault_raised"]
    assert t["scope"] == "joint" and t["scope_limbs"] == []
