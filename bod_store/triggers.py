"""Deterministic trigger detection. Each trigger = context block for NOVA + a safe action that
runs regardless of what NOVA says. Field names documented in TRIGGER_FIELDS.md."""
import json, sqlite3
from config import load_config
from rag_adapter import body_status, body_status_block, connect_ro, SnapshotError
import bod_ids
from store import joint_scope_ok, POWER_FAULT_KINDS

SAFE_ACTIONS = {
    "estop_active": "DEENERGIZE_TO_HARD_STOPS",     # R5: SW1 overrides everything; scope=body
    "joint_unknown": "HOLD",                        # freeze; no motion on unknown channels
    "fault_raised": "DEENERGIZE_TO_HARD_STOPS",     # scope joint|limb|body, see fault_scope()
    "goal_requested": "REFUSE_MOVE",                # when any check fails; else PASS_TO_VALIDATOR
    "state_stale": "HOLD",
    "power_cut_rehome_needed": "REQUEST_REHOME",
}
SCOPE_RANK = {"joint": 0, "limb": 1, "body": 2}

def fault_scope(faults, estop=False):
    """Scope rule for DEENERGIZE_TO_HARD_STOPS (documented in TRIGGER_FIELDS.md):
    - body: E-stop active, or any power fault (kind in POWER_FAULT_KINDS).
    - joint: ONLY when every faulted joint is not gravity-loaded and has no dependent joints
      (fingers, wrist rotation, jaw, eyes).
    - limb: default for every other fault (cutting one joint on a gravity-loaded arm can drop the
      rest of the limb onto it). Several faults -> the widest scope wins."""
    if estop or any(k in POWER_FAULT_KINDS for _, k in faults):
        return "body"
    return "joint" if faults and all(joint_scope_ok(j) for j, _ in faults) else "limb"

def _trig(event, severity, joints, reason, status, block, action=None):
    return {"trigger_event": event, "severity": severity, "joints": joints, "reason": reason,
            "safe_action": action or SAFE_ACTIONS[event], "safe_action_overrides_llm": True,
            "pose_id": status.get("pose_id"), "nova_context_block": block}

def _limbs(joints):
    return sorted({j.split(".")[0] for j in joints})

def goal_checks(goal, s, db_path=None, now=None, stale_after_s=None):
    """Return the list of failed checks (empty = every check passes)."""
    blockers = []
    gid = str(goal.get("pose_id", ""))
    if not gid.startswith("BP1-"):
        return ["goal not a BP1- pose_id"]
    if s.get("snapshot_status", "ok") != "ok":
        return [s["snapshot_status"]]
    try:
        con = connect_ro(db_path)
    except (SnapshotError, sqlite3.Error) as e:   # SnapshotError / sqlite3.Error => fail closed
        return [getattr(e, "status", "snapshot_corrupt")]
    try:
        row = con.execute("SELECT record_json FROM pose_library WHERE pose_id=?", (gid,)).fetchone()
        cur = dict((r[0], r[1]) for r in con.execute("SELECT joint,status FROM current_state"))
        rec_cur = con.execute("SELECT value FROM meta WHERE key='pose_record'").fetchone()
    except sqlite3.Error:
        return ["snapshot_corrupt"]
    finally:
        con.close()
    if not row:
        return ["goal pose_id not in pose_library"]
    rec = json.loads(row[0])
    if bod_ids.pose_id(rec) != gid or rec.get("pose_id") != gid:
        blockers.append("goal pose_id does not recompute")
    if bod_ids.remint(rec).get("checksums") != rec.get("checksums"):
        blockers.append("goal checksum does not recompute")
    if s["estop"]: blockers.append("estop active")
    if not s["motion_allowed"]: blockers.append("motion_allowed false")
    if s["unknown"] or s["rehome_required"]: blockers.append("unknown joints/rehome")
    if s["fault"]: blockers.append("faults")
    cur_rec = json.loads(rec_cur[0]) if rec_cur else {}
    if cur_rec.get("calibration_id") != rec.get("calibration_id") or cur_rec.get("body_profile") != rec.get("body_profile"):
        blockers.append("calibration_id/body_profile differs from current state")
    limits = load_config().get("calibration_limits", {}).get(rec.get("calibration_id"))
    targets = [j for j in rec["joint_vector"] if j["status"] != "absent"]
    if limits is None:
        blockers.append(f"no calibration limits for {rec.get('calibration_id')}")
    else:
        for j in targets:
            lim = limits.get(j["name"])
            q = j.get("q_deg")
            if lim is None:
                blockers.append(f"no calibration limit for {j['name']}")
            elif q is None or not (lim[0] <= q <= lim[1]):
                blockers.append(f"{j['name']} target {q} outside limits {lim}")
    # OPEN-16c: refuse goals whose library record itself encodes bad joint statuses
    for j in rec["joint_vector"]:
        st = j.get("status")
        if st in ("weak", "stuck", "disconnected", "dead", "stall", "fault"):
            blockers.append(f"goal joint {j['name']} faulted ({st})")
        elif st == "offline":
            blockers.append(f"goal joint {j['name']} offline")
        elif st == "unknown":
            blockers.append(f"goal joint {j['name']} unknown")
        elif st == "absent" and j["name"] not in ("head.rollNeck", "torso.midStom"):
            # unexpected absent in a goal record (interim always-absent channels are ok)
            blockers.append(f"goal joint {j['name']} absent")
    for j in targets:
        if cur.get(j["name"]) in (None, "absent"):
            blockers.append(f"target joint {j['name']} absent")
        elif cur.get(j["name"]) == "off":
            blockers.append(f"target joint {j['name']} switched off")
        elif cur.get(j["name"]) == "unknown":
            blockers.append(f"target joint {j['name']} unknown")
    s2 = body_status(db_path, now, stale_after_s, joints=[j["name"] for j in targets])
    if s2.get("snapshot_status", "ok") != "ok":
        return [s2["snapshot_status"]]
    if s2["stale"]:
        blockers.append("stale state (oldest target joint verification)")
    return blockers

def detect(db_path=None, now=None, goal=None, stale_after_s=None):
    """goal: optional dict {"pose_id": "BP1-...", "requested_by": "nova"}. Never raises on DB errors:
    any sqlite3.Error anywhere in the read path => snapshot_corrupt fail-closed output."""
    try:
        return _detect(db_path, now, goal, stale_after_s)
    except sqlite3.Error as e:
        from rag_adapter import _fail_closed
        cfg = load_config()
        sa = cfg["stale_after_s"] if stale_after_s is None else stale_after_s
        s = _fail_closed("snapshot_corrupt", f"snapshot_corrupt: {e}", sa, int(cfg.get("calibration_unapplied") or 0),
                         cfg.get("calibration_measured_error"))
        block = ("[BOD body status | read-only | LLM may not command motion]\nNO SNAPSHOT: snapshot_corrupt. "
                 "State STALE - HOLD; estop=UNKNOWN motion_allowed=false.")
        out = [_trig("state_stale", "medium", [], s["snapshot_error"], s, block)]
        if goal is not None:
            t = _trig("goal_requested", "high", [], "refused: snapshot_corrupt", s, block)
            t["goal_pose_id"] = goal.get("pose_id"); t["failed_checks"] = ["snapshot_corrupt"]; out.append(t)
        return out

def _detect(db_path=None, now=None, goal=None, stale_after_s=None):
    s = body_status(db_path, now, stale_after_s)
    block = body_status_block(db_path=db_path, now=now, stale_after_s=stale_after_s)
    out = []
    if s["estop"]:
        t = _trig("estop_active", "high", [], "E-stop SW1 active (R5)", s, block)
        t.update(scope="body", scope_limbs=[]); out.append(t)
    if s["rehome_required"]:
        out.append(_trig("power_cut_rehome_needed", "high", s["unknown"], "power cut: joints unknown until re-homed (OPEN-W24)", s, block))
    if s["unknown"]:
        out.append(_trig("joint_unknown", "high", s["unknown"], "joint position cannot be trusted", s, block))
    if s["fault"]:
        faults = [tuple(f.split(":", 1)) for f in s["fault"]]
        scope = fault_scope(faults, s["estop"])
        t = _trig("fault_raised", "high", [j for j, _ in faults], f"servo fault present (scope {scope})", s, block)
        # scope_limbs empty for body AND joint; only limb scope lists limbs
        t.update(scope=scope, scope_limbs=[] if scope in ("body", "joint") else _limbs([j for j, _ in faults]))
        out.append(t)
    if s["stale"]:
        age_msg = (f"{s['snapshot_status']}: {s.get('snapshot_error')}" if s.get("snapshot_status", "ok") != "ok"
                   else "never verified" if s["age_s"] is None
                   else f"oldest verification age {s['age_s']}s > {s['stale_after_s']}s")
        if s.get("snapshot_stale") and s.get("snapshot_age_s") is not None:
            age_msg += f"; snapshot age {s['snapshot_age_s']}s > {s['stale_after_s']}s"
        out.append(_trig("state_stale", "medium", s.get("stale_joints", []), age_msg, s, block))
    if goal is not None:
        blockers = goal_checks(goal, s, db_path, now, stale_after_s)
        t = _trig("goal_requested", "info" if not blockers else "high", [],
                  "refused: " + ", ".join(blockers) if blockers else "all goal checks passed; hand to deterministic validator",
                  s, block, None if blockers else "PASS_TO_VALIDATOR")
        t["goal_pose_id"] = goal.get("pose_id")
        t["failed_checks"] = blockers
        out.append(t)
    return out
