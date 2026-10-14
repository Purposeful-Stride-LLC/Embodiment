"""READ-ONLY adapter for NOVA RAG. Opens the BOD store with sqlite URI mode=ro.
Produces a compact status block NOVA can paste into its prompt. Does not import NOVA.

OPEN-17: never writes calibration/overrides.log (or any file). Unapplied measured
overrides are flagged in the status block instead of being logged here.
"""
import json, os, re, sqlite3, time
from pathlib import Path
from config import load_config

# OPEN-18(a): NOVA reads ONLY the operator snapshot file (config `nova_snapshot_path`, env
# BOD_SNAPSHOT_PATH, resolved relative to the install dir), written by the writer side with
# SimStore.write_snapshot() (non-WAL, atomic swap). Every NOVA-side open uses
# `file:...?mode=ro&immutable=1` (O_RDONLY, no -wal/-shm/journal). NOVA never opens the live db.
# Missing / unreadable / corrupt snapshot => fail closed: status snapshot_* , stale, no motion.

class SnapshotError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg); self.status = status

def snapshot_path(cfg=None):
    cfg = cfg or load_config()
    p = cfg.get("nova_snapshot_path")
    return Path(p).resolve() if p else None

def _ro_uri(p):
    return f"{Path(p).as_uri()}?mode=ro&immutable=1"

def snapshot(db_path=None):
    """Return (in-memory sqlite connection, snapshot_taken_at = snapshot file mtime).
    db_path is accepted for caller compatibility but NEVER opened: only the snapshot file is read."""
    p = snapshot_path()
    if p is None:
        raise SnapshotError("snapshot_missing", "no nova_snapshot_path configured")
    if not p.exists():
        raise SnapshotError("snapshot_missing", f"snapshot not found: {p}")
    if not os.access(p, os.R_OK):
        raise SnapshotError("snapshot_unreadable", f"snapshot not readable: {p}")
    taken = p.stat().st_mtime
    mem = sqlite3.connect(":memory:")
    try:
        src = sqlite3.connect(_ro_uri(p), uri=True)
        try:
            # OPEN-18(a)-2: full b-tree/page integrity (all tables+indexes), read-only on the immutable conn
            try:
                qc = [r[0] for r in src.execute("PRAGMA quick_check").fetchall()]
            except sqlite3.DatabaseError as e:   # quick_check itself can raise on a garbage page
                if "unable to open" in str(e):
                    raise
                qc = [f"error: {e}"]
            if qc != ["ok"]:
                raise sqlite3.DatabaseError("quick_check: " + "; ".join(map(str, qc[:3])))
            src.backup(mem)
        finally:
            src.close()
        mem.execute("SELECT count(*) FROM current_state").fetchone()
        mem.execute("SELECT count(*) FROM meta").fetchone()
    except sqlite3.Error as e:
        mem.close()
        status = "snapshot_unreadable" if "unable to open" in str(e) else "snapshot_corrupt"
        raise SnapshotError(status, f"{status}: {e}")
    mem.execute("PRAGMA query_only=ON")
    return mem, taken

def connect_ro(db_path=None):
    """In-memory copy of the operator snapshot (see snapshot()). Never opens the live db."""
    return snapshot(db_path)[0]

def _fail_closed(status, msg, stale_after_s, unapplied, merr):
    return {"pose_id": None, "evidence": "none", "stale": True, "age_s": None, "unknown": [], "fault": [],
            "absent": [], "off": [], "rehome_required": False, "stale_after_s": stale_after_s,
            "library_match": False, "estop": None, "motion_allowed": False, "snapshot_status": status,
            "snapshot_error": msg, "oldest_verified_at": None, "oldest_joint_verified_at": None,
            "last_write_at": None, "stale_joints": [], "write_age_s": None, "calibration_unapplied": unapplied,
            "snapshot_taken_at": None, "snapshot_age_s": None, "snapshot_stale": True,
            "stale_driver": status, "measured_error": merr}

def body_status(db_path=None, now=None, stale_after_s=None, joints=None):
    try:
        return _body_status(db_path, now, stale_after_s, joints)
    except sqlite3.Error as e:
        cfg = load_config()
        return _fail_closed("snapshot_corrupt", f"snapshot_corrupt: {e}",
                            cfg["stale_after_s"] if stale_after_s is None else stale_after_s,
                            int(cfg.get("calibration_unapplied") or 0), cfg.get("calibration_measured_error"))

def _body_status(db_path=None, now=None, stale_after_s=None, joints=None):
    """joints: optional list of the joints that matter (e.g. goal targets). Default = every live
    (ok/fault) joint. Staleness uses the OLDEST verified_at among them (per joint, §9 #3);
    a joint never verified (verified_at NULL) is stale."""
    cfg = load_config()
    stale_after_s = cfg["stale_after_s"] if stale_after_s is None else stale_after_s
    now = time.time() if now is None else now
    unapplied = int(cfg.get("calibration_unapplied") or 0)
    merr = cfg.get("calibration_measured_error")
    try:
        con, snap_at = snapshot(db_path)
    except SnapshotError as e:
        return _fail_closed(e.status, str(e), stale_after_s, unapplied, merr)
    try:
        rows = con.execute("SELECT joint,pose_id,pose_name,body_profile,angle_deg,confidence,status,detail,source,"
                           "verified_at,last_write_at FROM current_state ORDER BY joint").fetchall()
        meta = dict(con.execute("SELECT key,value FROM meta").fetchall())
        lib = {r[0] for r in con.execute("SELECT pose_id FROM pose_library")}
    except sqlite3.Error as e:   # defense in depth: any mid-read DB error => fail closed
        return _fail_closed("snapshot_corrupt", f"snapshot_corrupt: {e}", stale_after_s, unapplied, merr)
    finally:
        con.close()
    estop = meta.get("estop", "released") == "active"
    motion_allowed = meta.get("motion_allowed", "0") == "1"
    if not rows:
        return {"pose_id": None, "evidence": "none", "stale": True, "age_s": None, "unknown": [], "fault": [],
                "absent": [], "off": [], "rehome_required": True, "stale_after_s": stale_after_s,
                "library_match": False, "estop": estop, "motion_allowed": False,
                "oldest_verified_at": None, "last_write_at": None, "stale_joints": [], "write_age_s": None,
                "calibration_unapplied": unapplied, "snapshot_taken_at": snap_at,
                "snapshot_age_s": round(max(0.0, now - snap_at), 1), "measured_error": merr,
                "snapshot_status": "ok", "snapshot_stale": now - snap_at > stale_after_s,
                "oldest_joint_verified_at": None, "stale_driver": "no_state"}
    by = lambda s: [r[0] for r in rows if r[6] == s]
    live = [r for r in rows if r[6] in ("ok", "fault")]
    sources = {r[8] for r in live}
    evidence = ("measured" if sources == {"measured"} else
                "sim(open-loop commanded)" if "sim" in sources else "commanded(open-loop)") if live else "none"
    matter = [r for r in rows if (r[0] in joints if joints is not None else r[6] in ("ok", "fault"))]
    vers = [r[9] for r in matter]
    never = any(v is None for v in vers)
    all_unknown = not any(r[6] in ("ok", "fault", "off") for r in rows)
    oldest_joint = None if (never or not vers) else min(vers)
    # state age = OLDER of snapshot copy time and oldest verified joint (a stale snapshot is stale)
    oldest = None if oldest_joint is None else min(oldest_joint, snap_at)
    age = None if oldest is None else now - oldest
    snap_stale = now - snap_at > stale_after_s
    stale_joints = [r[0] for r in matter if r[9] is None or now - min(r[9], snap_at) > stale_after_s]
    joint_age = None if oldest_joint is None else now - oldest_joint
    joint_stale = never or not matter or (joint_age is not None and joint_age > stale_after_s)
    driver = ",".join(x for x, f in (("oldest_joint", joint_stale), ("snapshot", snap_stale)) if f) or \
        ("all_unknown" if all_unknown else None)
    last_write = max(r[10] for r in rows)
    pid = rows[0][1]
    return {
        "pose_id": pid, "pose_name": "unknown" if all_unknown else rows[0][2], "body_profile": rows[0][3],
        "unknown": by("unknown"), "fault": [f"{r[0]}:{r[7]}" for r in rows if r[6] == "fault"],
        "absent": by("absent"), "off": by("off"), "ok_count": len(by("ok")),
        "min_conf": round(min((r[5] for r in live), default=0.0), 2),
        "evidence": evidence, "age_s": None if age is None else round(age, 1), "stale_after_s": stale_after_s,
        "stale": bool(stale_joints) or snap_stale or not matter or all_unknown,
        "snapshot_taken_at": snap_at, "snapshot_age_s": round(max(0.0, now - snap_at), 1), "snapshot_stale": snap_stale,
        "oldest_joint_verified_at": oldest_joint, "snapshot_status": "ok", "stale_driver": driver,
        "oldest_joint_age_s": None if joint_age is None else round(joint_age, 1), "measured_error": merr, "stale_joints": stale_joints,
        "oldest_verified_at": oldest, "last_write_at": last_write, "write_age_s": round(now - last_write, 1),
        "rehome_required": meta.get("rehome_required") == "1",
        "library_match": (pid in lib) and not all_unknown, "estop": estop, "motion_allowed": motion_allowed,
        "calibration_unapplied": unapplied,
    }

def _one_line(text):
    """Flatten newlines/CR/tabs to spaces and collapse runs so each block field stays one line."""
    return re.sub(r"[ \t\r\n]+", " ", str(text)).strip()

def _trim(items, n=6):
    return ",".join(items[:n]) + (f",+{len(items)-n}" if len(items) > n else "") if items else "none"

def body_status_block(max_tokens=200, db_path=None, now=None, stale_after_s=None):
    s = body_status(db_path, now, stale_after_s)
    lines = ["[BOD body status | read-only | LLM may not command motion]"]
    if s.get("snapshot_status", "ok") != "ok":
        lines.append(f"NO SNAPSHOT: {s['snapshot_status']} ({_one_line(s['snapshot_error'])}). State STALE - HOLD; "
                     "estop=UNKNOWN motion_allowed=false. Operator must write a snapshot (SimStore.write_snapshot).")
    elif s["pose_id"] is None:
        lines.append("NO STATE: store empty. Treat whole body as UNKNOWN; re-home required.")
    else:
        lines += [f"pose={s['pose_id']} ({s['pose_name']}, {s['body_profile']})"
                  + ("" if s["library_match"] else " [not a library pose]"),
                  f"evidence={s['evidence']} min_conf={s['min_conf']} ok={s['ok_count']}",
                  f"unknown={_trim(s['unknown'])}", f"fault={_trim(s['fault'])}", f"absent={_trim(s['absent'])}",
                  f"off={_trim(s['off'])}",
                  f"estop={'ACTIVE' if s['estop'] else 'released'} motion_allowed={str(s['motion_allowed']).lower()}",
                  f"oldest_joint_age={'never' if s['oldest_joint_age_s'] is None else str(s['oldest_joint_age_s']) + 's'}"
                  f" snapshot_age={s['snapshot_age_s']}s last_write_age={s['write_age_s']}s"
                  + (f" STALE(>{s['stale_after_s']}s, driven by {s['stale_driver']}) - do not trust pose"
                     if s["stale"] else "")]
        if s["rehome_required"]:
            lines.append("REHOME REQUIRED (power cut): no motion except supervised homing.")
    if s.get("measured_error"):
        lines.append(f"calibration: REJECTED ({_one_line(s['measured_error'])}); overrides ignored")
    if s.get("calibration_unapplied"):
        lines.append(f"calibration: {s['calibration_unapplied']} unapplied overrides")
    text = "\n".join(_one_line(ln) for ln in lines)
    limit = max_tokens * 4  # ~4 chars/token heuristic
    return text if len(text) <= limit else text[: limit - 3] + "..."

def body_status_json(db_path=None, now=None, stale_after_s=None):
    return json.dumps(body_status(db_path, now, stale_after_s), sort_keys=True, separators=(",", ":"))
