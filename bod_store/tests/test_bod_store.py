import sqlite3, sys, json, subprocess, os
import pytest
from rag_adapter import connect_ro, body_status, body_status_block, body_status_json
from triggers import detect

def test_wal_and_library(env):
    con = sqlite3.connect(env["db"])
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert con.execute("SELECT COUNT(*) FROM pose_library WHERE pose_id LIKE 'BP1-%'").fetchone()[0] == 8
    assert con.execute("SELECT COUNT(*) FROM pose_library WHERE pose_id LIKE 'BT1-%' OR pose_id LIKE 'BPL1-%'").fetchone()[0] == 0

def test_config_path_from_env(env):
    from config import load_config
    assert load_config()["db_path"] == env["db"]

def test_readonly_cannot_write(env):
    env["st"].update_from_sim(env["recs"][0])
    con = connect_ro(env["db"])
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("UPDATE current_state SET status='ok'")
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO events(ts,kind) VALUES (1,'x')")

def test_events_append_only(env):
    env["st"].update_from_sim(env["recs"][0])
    con = sqlite3.connect(env["db"])
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("DELETE FROM events")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("UPDATE events SET kind='x'")

def test_staleness(env):
    env["st"].rehome(env["recs"][0]["pose_id"])  # open-loop sim write alone never verifies (§9 #3 fix)
    assert not body_status(now=1002.0, stale_after_s=5)["stale"]
    s = body_status(now=1010.0, stale_after_s=5)
    assert s["stale"] and s["age_s"] == 10.0
    assert "STALE" in body_status_block(now=1010.0, stale_after_s=5)
    assert "STALE" not in body_status_block(now=1001.0, stale_after_s=5)

def test_absent_joints(env):
    env["st"].update_from_sim(env["recs"][0])
    s = body_status(now=1000)
    assert {"head.rollNeck", "torso.midStom"} <= set(s["absent"])
    assert not any(x.startswith(("head.rollNeck", "torso.midStom")) for x in s["fault"] + s["unknown"])
    with pytest.raises(ValueError):
        env["st"].raise_fault("torso.midStom")

def test_open_loop_confidence_honest(env):
    env["st"].update_from_sim(env["recs"][0])
    s = body_status(now=1000)
    assert s["min_conf"] <= 0.6 and "open-loop" in s["evidence"]

def test_unknown_after_power_cut_until_rehome(env):
    st, r = env["st"], env["recs"]
    st.update_from_sim(r[0]); pid0 = r[0]["pose_id"]
    pid = st.mark_unknown(None, reason="power_cut")
    s = body_status(now=1000)
    assert pid != pid0 and s["rehome_required"] and s["ok_count"] == 0
    assert "torso.midStom" in s["absent"] and "rightArm.bicep" in s["unknown"]
    con = sqlite3.connect(env["db"])
    assert con.execute("SELECT COUNT(*) FROM current_state WHERE status='unknown' AND angle_deg IS NOT NULL").fetchone()[0] == 0
    st.rehome(pid0)
    s = body_status(now=1000)
    assert not s["unknown"] and not s["rehome_required"] and s["pose_id"] == pid0

def test_autodisable_single_joint(env):
    env["st"].update_from_sim(env["recs"][0])
    env["st"].mark_unknown(["leftArm.shoulder"])
    assert body_status(now=1000)["unknown"] == ["leftArm.shoulder"]

def _events(trigs): return {t["trigger_event"]: t for t in trigs}

@pytest.mark.parametrize("event,setup,action", [
    ("joint_unknown", lambda st: st.mark_unknown(["rightArm.bicep"]), "HOLD"),
    ("fault_raised", lambda st: st.raise_fault("rightArm.shoulder", "stall"), "DEENERGIZE_TO_HARD_STOPS"),
    ("power_cut_rehome_needed", lambda st: st.mark_unknown(None, reason="power_cut"), "REQUEST_REHOME"),
])
def test_trigger_fires(env, event, setup, action):
    env["st"].update_from_sim(env["recs"][0]); setup(env["st"])
    t = _events(detect(now=1000))[event]
    assert t["safe_action"] == action and t["safe_action_overrides_llm"] and "[BOD" in t["nova_context_block"]

def test_trigger_state_stale(env):
    env["st"].rehome(env["recs"][0]["pose_id"])
    assert "state_stale" not in _events(detect(now=1001, stale_after_s=5))
    assert _events(detect(now=1100, stale_after_s=5))["state_stale"]["safe_action"] == "HOLD"

def test_trigger_goal_requested(env):
    st, r = env["st"], env["recs"]
    st.rehome(r[0]["pose_id"])
    assert _events(detect(now=1000, goal={"pose_id": r[1]["pose_id"]}))["goal_requested"]["safe_action"] == "PASS_TO_VALIDATOR"
    assert _events(detect(now=1000, goal={"pose_id": "move arm up"}))["goal_requested"]["safe_action"] == "REFUSE_MOVE"
    st.mark_unknown(None, reason="power_cut")
    g = _events(detect(now=1000, goal={"pose_id": r[1]["pose_id"]}))["goal_requested"]
    assert g["safe_action"] == "REFUSE_MOVE" and "unknown" in g["reason"]

def test_fault_examples_map_to_fault(env):
    env["st"].update_from_sim(env["recs"][4])  # rest+LB_stuck45
    s = body_status(now=1000)
    assert any(f.startswith("leftArm.bicep:stuck") for f in s["fault"])

def test_block_budget_and_json(env):
    for r in env["recs"]:
        env["st"].update_from_sim(r)
        assert len(body_status_block(max_tokens=200, now=1000)) <= 800
    assert json.loads(body_status_json(now=1000))["pose_id"] == env["recs"][-1]["pose_id"]

def test_runs_without_nova(env):
    code = ("import sys,builtins;real=builtins.__import__\n"
            "def g(n,*a,**k):\n  assert not n.lower().startswith('nova'), n\n  return real(n,*a,**k)\n"
            "builtins.__import__=g\nimport sim_driver,triggers,rag_adapter;sim_driver.main()\n")
    home = os.path.dirname(os.path.dirname(__file__))
    p = subprocess.run([sys.executable, "-c", code], cwd=home, env={**os.environ}, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "power cut" in p.stdout and "REQUEST_REHOME" in p.stdout
    assert not any("NOVA.db" in str(x) for x in os.listdir(os.path.dirname(env["db"])))

def test_empty_store_is_unknown(env):
    s = body_status(now=1000)
    assert s["pose_id"] is None and s["rehome_required"]
