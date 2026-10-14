"""Fixes for BOD_DECISION_TRIGGERS.md §9 #3-#7 + goal-check gaps (review). SIM ONLY."""
import copy, json, sqlite3
import pytest
from rag_adapter import body_status, body_status_block
from triggers import detect, fault_scope

def _ev(trigs): return {t["trigger_event"]: t for t in trigs}
def _goal(now=1000, pid=None, env=None):
    return _ev(detect(now=now, goal={"pose_id": pid or env["recs"][1]["pose_id"]}))["goal_requested"]

@pytest.fixture
def ready(env):
    env["st"].rehome(env["recs"][0]["pose_id"])   # verified, estop released, motion_allowed true
    return env

# ---------- 1. goal checks ----------
def test_goal_all_checks_pass(ready):
    g = _goal(env=ready)
    assert g["safe_action"] == "PASS_TO_VALIDATOR" and g["failed_checks"] == []

def test_goal_neg_not_bp1(ready):
    assert _goal(pid="raise arm", env=ready)["safe_action"] == "REFUSE_MOVE"

def test_goal_neg_not_in_library(ready):
    g = _goal(pid="BP1-0123456789abcdef", env=ready)
    assert g["safe_action"] == "REFUSE_MOVE" and "not in pose_library" in g["reason"]

def _tamper(env, fn):
    con = sqlite3.connect(env["db"]); pid = env["recs"][1]["pose_id"]
    rec = json.loads(con.execute("SELECT record_json FROM pose_library WHERE pose_id=?", (pid,)).fetchone()[0])
    fn(rec)
    con.execute("UPDATE pose_library SET record_json=? WHERE pose_id=?", (json.dumps(rec), pid)); con.commit(); con.close()
    env["st"].write_snapshot()  # OPEN-18a: NOVA reads only the snapshot

def test_goal_neg_id_does_not_recompute(ready):
    _tamper(ready, lambda r: r["joint_vector"][6].update(q_deg=45))  # payload changed, id kept
    g = _goal(env=ready)
    assert g["safe_action"] == "REFUSE_MOVE" and "pose_id does not recompute" in g["reason"]

def test_goal_neg_checksum_does_not_recompute(ready):
    _tamper(ready, lambda r: r.update(notes="edited"))  # record_checksum stale, pose_id still fine
    g = _goal(env=ready)
    assert g["safe_action"] == "REFUSE_MOVE" and "checksum does not recompute" in g["reason"]
    assert "pose_id does not recompute" not in g["reason"]

def test_goal_neg_estop(ready):
    ready["st"].set_estop(True)
    g = _goal(env=ready)
    assert g["safe_action"] == "REFUSE_MOVE"
    assert "estop active" in g["failed_checks"]
    # set_estop(True) also forces motion_allowed=0 (OPEN-16a)
    assert "motion_allowed false" in g["failed_checks"]

def test_goal_neg_motion_not_allowed(ready):
    ready["st"].set_motion_allowed(False)
    g = _goal(env=ready)
    assert g["safe_action"] == "REFUSE_MOVE" and g["failed_checks"] == ["motion_allowed false"]

def test_motion_allowed_follows_record(env):
    env["st"].rehome(env["recs"][3]["pose_id"])  # E4 gear slip: motion_allowed false
    assert body_status(now=1000)["motion_allowed"] is False

def test_goal_neg_outside_limits(ready, monkeypatch):
    import config
    real = config.load_config
    def fake():
        c = real(); c["calibration_limits"] = copy.deepcopy(c["calibration_limits"])
        c["calibration_limits"]["cal-classic31-nixie-fullstart-defaults-v0"]["leftArm.bicep"] = [0, 5]
        return c
    monkeypatch.setattr("triggers.load_config", fake)
    g = _goal(env=ready)  # ready: leftArm.bicep = 65
    assert g["safe_action"] == "REFUSE_MOVE" and g["failed_checks"] == ["leftArm.bicep target 65 outside limits [0, 5]"]

def test_goal_neg_limit_missing(ready, monkeypatch):
    import config
    real = config.load_config
    def fake():
        c = real(); c["calibration_limits"] = copy.deepcopy(c["calibration_limits"])
        del c["calibration_limits"]["cal-classic31-nixie-fullstart-defaults-v0"]["head.jaw"]
        return c
    monkeypatch.setattr("triggers.load_config", fake)
    g = _goal(env=ready)
    assert g["safe_action"] == "REFUSE_MOVE" and g["failed_checks"] == ["no calibration limit for head.jaw"]

def test_goal_neg_calibration_missing(env, monkeypatch):
    import config
    real = config.load_config
    def fake():
        c = real(); c["calibration_limits"] = copy.deepcopy(c["calibration_limits"])
        del c["calibration_limits"]["cal-fingerstarter-testscript-map-0-180-42-160"]
        return c
    monkeypatch.setattr("triggers.load_config", fake)
    st, r = env["st"], env["recs"]
    st.rehome(r[6]["pose_id"])  # finger_starter: limits removed for this negative
    g = _goal(pid=r[6]["pose_id"], env=env)
    assert g["safe_action"] == "REFUSE_MOVE" and any("no calibration limits" in x for x in g["failed_checks"])

def test_goal_neg_target_joint_absent(ready):
    ready["st"].con.execute("UPDATE current_state SET status='absent', angle_deg=NULL WHERE joint='head.jaw'")
    ready["st"].con.commit(); ready["st"].write_snapshot()
    g = _goal(env=ready)
    assert g["safe_action"] == "REFUSE_MOVE" and "target joint head.jaw absent" in g["failed_checks"]

def test_goal_neg_target_joint_missing_row(ready):
    ready["st"].con.execute("DELETE FROM current_state WHERE joint='head.eyeX'"); ready["st"].con.commit(); ready["st"].write_snapshot()
    g = _goal(env=ready)
    assert "target joint head.eyeX absent" in g["failed_checks"]

def test_goal_neg_current_unknown(ready):
    ready["st"].mark_unknown(["leftArm.shoulder"])
    g = _goal(env=ready)
    assert g["safe_action"] == "REFUSE_MOVE" and "unknown joints/rehome" in g["failed_checks"]

def test_goal_neg_current_fault(ready):
    ready["st"].raise_fault("rightHand.index", "weak")
    g = _goal(env=ready)
    assert g["safe_action"] == "REFUSE_MOVE" and g["failed_checks"] == ["faults"]

def test_goal_neg_stale(ready):
    g = _goal(now=1100, env=ready)
    assert g["safe_action"] == "REFUSE_MOVE" and any("stale" in x for x in g["failed_checks"])

def test_goal_neg_target_switched_off(ready):
    ready["st"].switch_off("head.jaw")
    g = _goal(env=ready)
    assert g["safe_action"] == "REFUSE_MOVE" and "target joint head.jaw switched off" in g["failed_checks"]

# ---------- 2. staleness ----------
def test_one_fresh_joint_does_not_hide_stale_ones(ready):
    st, t = ready["st"], ready["t"]
    con = st.con
    con.execute("UPDATE current_state SET verified_at=1090, source='measured' WHERE joint='head.eyeX'"); con.commit(); st.write_snapshot()
    s = body_status(now=1095, stale_after_s=5)
    assert s["stale"] and s["age_s"] == 95.0 and "head.eyeX" not in s["stale_joints"]
    assert "rightArm.bicep" in s["stale_joints"]

def test_sim_write_keeps_verified_at_at_rehome(ready):
    st, t, r = ready["st"], ready["t"], ready["recs"]
    t[0] = 1050.0
    st.update_from_sim(r[1])  # open-loop sim write
    row = st.con.execute("SELECT verified_at,last_write_at FROM current_state WHERE joint='rightArm.bicep'").fetchone()
    assert row == (1000.0, 1050.0)
    s = body_status(now=1051, stale_after_s=5)
    assert s["oldest_verified_at"] == 1000.0 and s["last_write_at"] == 1050.0 and s["stale"]
    b = body_status_block(now=1051, stale_after_s=5)
    assert "oldest_joint_age=51.0s" in b and "last_write_age=1.0s" in b

def test_measured_value_sets_verified_at(ready):
    st, t, r = ready["st"], ready["t"], ready["recs"]
    rec = copy.deepcopy(r[0])
    for j in rec["joint_vector"]:
        if j["status"] == "ok": j["sensed_deg"] = j["commanded_deg"]
    t[0] = 1200.0
    st.update_from_sim(rec)
    s = body_status(now=1201, stale_after_s=5)
    assert not s["stale"] and s["evidence"] == "measured"

def test_never_verified_is_stale(env):
    env["st"].update_from_sim(env["recs"][0])
    s = body_status(now=1000)
    assert s["stale"] and s["age_s"] is None and "oldest_joint_age=never" in body_status_block(now=1000)

def test_mark_unknown_and_fault_do_not_verify(ready):
    st, t = ready["st"], ready["t"]
    t[0] = 1100.0; st.raise_fault("rightArm.shoulder", "stall")
    assert st.con.execute("SELECT verified_at,last_write_at FROM current_state WHERE joint='rightArm.shoulder'").fetchone() == (1000.0, 1100.0)

# ---------- 3. pose name after power cut / all unknown ----------
def test_power_cut_pose_name_unknown_no_library_match(ready):
    ready["st"].mark_unknown(None, reason="power_cut")
    s = body_status(now=1000)
    assert s["pose_name"] == "unknown" and not s["library_match"]
    assert ready["st"].current_record()["pose_name"] == "unknown"
    names = {r[0] for r in ready["st"].con.execute("SELECT DISTINCT pose_name FROM current_state")}
    assert names == {"unknown"}
    assert "(unknown," in body_status_block(now=1000) and "[not a library pose]" in body_status_block(now=1000)

def test_all_unknown_without_power_cut(ready):
    st = ready["st"]
    joints = [r[0] for r in st.con.execute("SELECT joint FROM current_state WHERE status!='absent'")]
    st.mark_unknown(joints, reason="autoDisable")
    s = body_status(now=1000)
    assert s["pose_name"] == "unknown" and not s["library_match"] and not s["rehome_required"]

def test_library_match_true_after_rehome(ready):
    assert body_status(now=1000)["library_match"]

# ---------- 4. intentional off (code 5) ----------
def test_switch_off_not_a_fault(ready):
    st = ready["st"]
    st.switch_off("head.jaw")
    s = body_status(now=1000)
    assert s["off"] == ["head.jaw"] and s["fault"] == []
    assert "fault_raised" not in _ev(detect(now=1000))
    assert st.con.execute("SELECT COUNT(*) FROM faults").fetchone()[0] == 0
    assert st.current_record()["fault_mask"][24] == "5"

def test_raise_fault_offline_routes_to_off(ready):
    ready["st"].raise_fault("rightHand.wrist", "offline")
    assert "fault_raised" not in _ev(detect(now=1000))
    assert body_status(now=1000)["off"] == ["rightHand.wrist"]

def test_record_with_offline_is_off(env):
    env["st"].update_from_sim(env["recs"][2])  # E3 ready+RA_offline
    s = body_status(now=1000)
    assert s["off"] and not s["fault"] and "fault_raised" not in _ev(detect(now=1000))

# ---------- 5. DEENERGIZE_TO_HARD_STOPS scope ----------
@pytest.mark.parametrize("joint,kind,scope", [
    ("rightHand.index", "weak", "joint"), ("leftHand.wrist", "stall", "joint"), ("head.jaw", "stuck", "joint"),
    ("rightArm.shoulder", "stall", "limb"), ("rightArm.bicep", "stuck", "limb"),
    ("rightArm.rotate", "stuck", "limb"),  # not gravity, but carries the hand
    ("head.neck", "weak", "limb"), ("head.rothead", "stuck", "limb"),
    ("rightHand.index", "power", "body"), ("rightArm.shoulder", "brownout", "body"),
])
def test_fault_scope(ready, joint, kind, scope):
    ready["st"].raise_fault(joint, kind)
    t = _ev(detect(now=1000))["fault_raised"]
    assert t["scope"] == scope and t["safe_action"] == "DEENERGIZE_TO_HARD_STOPS"
    assert t["scope_limbs"] == ([] if scope in ("body", "joint") else [joint.split(".")[0]])

def test_fault_scope_widest_wins(ready):
    ready["st"].raise_fault("rightHand.index", "weak"); ready["st"].raise_fault("leftArm.bicep", "stuck")
    t = _ev(detect(now=1000))["fault_raised"]
    assert t["scope"] == "limb" and t["scope_limbs"] == ["leftArm", "rightHand"]

def test_estop_scope_body(ready):
    ready["st"].set_estop(True)
    t = _ev(detect(now=1000))["estop_active"]
    assert t["scope"] == "body" and t["safe_action"] == "DEENERGIZE_TO_HARD_STOPS"
    assert fault_scope([("rightHand.index", "weak")], estop=True) == "body"
    assert "estop=ACTIVE" in body_status_block(now=1000)

def test_block_budget_with_new_lines(ready):
    ready["st"].mark_unknown(None, reason="power_cut"); ready["st"].set_estop(True)
    assert len(body_status_block(now=1000)) <= 800
