"""SIM-ONLY write API for the BOD store. No hardware I/O. Deterministic code only; the LLM never calls this.
Open-loop servos: sim updates are source='sim' (or 'commanded'), confidence capped at 0.6 (spec §5)."""
import json, time
from init_db import connect_rw
import bod_ids

GRAVITY_SUFFIXES = ("bicep", "shoulder", "omoplate", "neck", "topStom", "midStom")
ALWAYS_ABSENT = ("head.rollNeck", "torso.midStom")  # spec v0.3 interim, OPEN-W9
OPEN_LOOP_CONF_CAP = 0.6
FAULT_DETAILS = ("offline", "weak", "stuck", "stall", "fault", "disconnected", "dead")

def is_gravity(joint):
    return joint.split(".")[-1] in GRAVITY_SUFFIXES and joint not in ("head.rollNeck",)

def _get_rec(con):
    r = con.execute("SELECT value FROM meta WHERE key='pose_record'").fetchone()
    return json.loads(r[0]) if r else None

def _set_fault_digits(rec, joint, code):
    fm = list(rec["fault_mask"])
    for jt in rec["joint_vector"]:
        if jt["name"] == joint:
            for s in jt.get("svo", []):
                fm[bod_ids.svo_index(rec["body_profile"], s)] = code
    rec["fault_mask"] = "".join(fm)

def _sim_pose_id(con, mutate=None):
    """Canonical re-mint (spec §2.8) of the sim-mutated record kept in meta.pose_record.
    Tagged as sim-derived in events; NOT a library pose and never written to NOVA."""
    rec = _get_rec(con)
    if mutate:
        mutate(rec)
    rec = bod_ids.remint(rec)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('pose_record', ?)", (json.dumps(rec, sort_keys=True),))
    return rec["pose_id"]

def _log(con, kind, joint=None, pose_id=None, **payload):
    con.execute("INSERT INTO events(ts,kind,joint,pose_id,payload) VALUES (?,?,?,?,?)",
                (time.time(), kind, joint, pose_id, json.dumps(payload, sort_keys=True)))

def _repose(con, name=None, mutate=None):
    pid = _sim_pose_id(con, mutate)
    con.execute("UPDATE current_state SET pose_id=?" + (", pose_name=?" if name else ""),
                (pid, name) if name else (pid,))
    return pid

def _apply_fault(rec, joint, kind):
    """Spec §2.4 rule 3 / §2.5: stuck/stall keeps its (stuck_at) q_deg; other faults -> q null."""
    code = bod_ids.FAULT_CODE.get(kind, "3")
    spec_status = {"0": "ok", "1": "weak", "2": "stuck", "3": "disconnected", "4": "unknown", "5": "offline"}[code]
    for jt in rec["joint_vector"]:
        if jt["name"] == joint:
            jt["status"] = spec_status
            if code != "2":
                jt["q_deg"] = None
    _set_fault_digits(rec, joint, code)

LIMB_SCOPE_EXEMPT = ("thumb", "index", "majeure", "ringFinger", "pinky", "wrist")  # hand channels
JOINT_SCOPE_HEAD = ("head.jaw", "head.eyeX", "head.eyeY")
POWER_FAULT_KINDS = ("power", "power_fault", "brownout", "rail", "rail_off")

def joint_scope_ok(joint):
    """True only for a joint that is NOT gravity-loaded AND carries no dependent joints
    (fingers, wrist rotation, jaw, eyes). Everything else escalates to limb scope."""
    return (not is_gravity(joint)) and (joint.split(".")[-1] in LIMB_SCOPE_EXEMPT and joint.split(".")[0].endswith("Hand")
                                        or joint in JOINT_SCOPE_HEAD)

def _insert_row(c, name, pid, pname, prof, ang, conf, status, detail, source, energized, gravity, verified_at, now):
    c.execute("INSERT INTO current_state(joint,pose_id,pose_name,body_profile,angle_deg,confidence,status,detail,"
              "source,energized,gravity,verified_at,last_write_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (name, pid, pname, prof, ang, conf, status, detail, source, energized, gravity, verified_at, now))

class SimStore:
    def __init__(self, db_path=None, clock=time.time, snapshot_path=None, auto_snapshot=True):
        """auto_snapshot: after every commit, write the NOVA snapshot (OPEN-18a) to snapshot_path
        (default: config nova_snapshot_path, relative to the install dir)."""
        from config import load_config
        self.con = connect_rw(db_path)
        self.clock = clock
        self.snapshot_path = snapshot_path or load_config().get("nova_snapshot_path")
        self.auto_snapshot = auto_snapshot and bool(self.snapshot_path)
        if self.auto_snapshot:
            self.write_snapshot()  # initial snapshot of the opened store

    def update_from_sim(self, record, now=None, verified=False):
        """Replace current_state from a bod.pose.v1 record (sim). Maps spec statuses:
        ok->ok, absent->absent, unknown->unknown, offline (digit 5, commanded off)->off,
        weak/stuck/disconnected->fault.
        verified_at: set to now only when the joint carries a measured value (sensed_deg) or
        verified=True (rehome). Otherwise the previous verified_at is kept (open-loop sim never verifies)."""
        now = self.clock() if now is None else now
        assert record["schema_version"] == "bod.pose.v1" and record["pose_id"].startswith("BP1-")
        if bod_ids.pose_id(record) != record["pose_id"]:
            raise ValueError("record pose_id does not match spec §2.8 canonical derivation")
        c = self.con
        prev_ver = dict(c.execute("SELECT joint, verified_at FROM current_state"))
        c.execute("INSERT OR REPLACE INTO meta VALUES ('pose_record', ?)", (json.dumps(record, sort_keys=True),))
        # E-stop is latched: a record may RAISE it, never clear it (OPEN-16a).
        prev = c.execute("SELECT value FROM meta WHERE key='estop'").fetchone()
        prev_active = bool(prev and prev[0] == "active")
        rec_active = record.get("health", {}).get("estop") == "active"
        estop_active = prev_active or rec_active
        c.execute("INSERT OR REPLACE INTO meta VALUES ('estop', ?)",
                  ("active" if estop_active else "released",))
        # motion_allowed cannot become true while e-stop is active
        rec_motion = record.get("balance_profile", {}).get("motion_allowed") is True
        c.execute("INSERT OR REPLACE INTO meta VALUES ('motion_allowed', ?)",
                  ("1" if (rec_motion and not estop_active) else "0",))
        c.execute("DELETE FROM current_state")
        for j in record["joint_vector"]:
            st = j["status"]
            measured = j.get("sensed_deg") is not None
            source = "measured" if measured else "sim"
            if j["name"] in ALWAYS_ABSENT or st == "absent":
                status, ang, conf = "absent", None, 0.0
            elif st == "ok":
                status = "ok"
                ang = j.get("sensed_deg") if measured else j.get("commanded_deg")
                conf = (j.get("confidence") or 0.0) if measured else min(j.get("confidence") or 0.0, OPEN_LOOP_CONF_CAP)
            elif st == "unknown":
                status, ang, conf = "unknown", None, 0.0
            elif st == "offline":
                status, ang, conf = "off", None, 0.0   # intentional (digit 5): not a fault
            else:
                status = "fault"
                ang = j.get("q_deg")  # e.g. stuck_at; may be None
                conf = min(j.get("confidence") or 0.0, OPEN_LOOP_CONF_CAP)
            ver = now if (verified or measured) and status not in ("unknown", "absent") else prev_ver.get(j["name"])
            _insert_row(c, j["name"], record["pose_id"], record["pose_name"], record["body_profile"], ang, conf,
                        status, st, source, int(bool(j.get("energized"))), int(is_gravity(j["name"])), ver, now)
            if status == "fault":
                c.execute("INSERT INTO faults(joint,kind,detail,raised_at) VALUES (?,?,?,?)",
                          (j["name"], st, "from sim record", now))
        _log(c, "update_from_sim", pose_id=record["pose_id"], pose_name=record["pose_name"], verified=verified)
        self._commit()
        return record["pose_id"]

    def set_estop(self, active, now=None):
        """SIM: raise E-stop SW1 (R5). Latched — only estop_release.release_estop(operator_id, reason)
        may clear it. set_estop(False) is refused so rag/triggers cannot release via this API.
        Raising also forces motion_allowed=0."""
        if not active:
            raise ValueError("e-stop release requires estop_release.release_estop(operator_id, reason)")
        self.con.execute("INSERT OR REPLACE INTO meta VALUES ('estop', 'active')")
        self.con.execute("INSERT OR REPLACE INTO meta VALUES ('motion_allowed', '0')")
        _log(self.con, "estop", active=True); self._commit()

    def set_motion_allowed(self, allowed, now=None):
        """SIM: balance_profile.motion_allowed gate. Cannot become true while e-stop is active."""
        est = self.con.execute("SELECT value FROM meta WHERE key='estop'").fetchone()
        if allowed and est and est[0] == "active":
            allowed = False
        self.con.execute("INSERT OR REPLACE INTO meta VALUES ('motion_allowed', ?)", ("1" if allowed else "0",))
        _log(self.con, "motion_allowed", allowed=bool(allowed)); self._commit()

    def switch_off(self, joint, reason="operator", now=None):
        """Intentional de-energise (spec digit 5 'offline'). Non-gravity: status='off', NOT a fault
        (no faults row, no fault_raised). Gravity-loaded joints cannot stay commanded-off safely:
        they become status='unknown' (digit 4) immediately so detect() fires joint_unknown (OPEN-16b)."""
        now = self.clock() if now is None else now
        c = self.con
        r = c.execute("SELECT status FROM current_state WHERE joint=?", (joint,)).fetchone()
        if r is None or r[0] == "absent":
            raise ValueError(f"cannot switch off absent/unknown channel {joint}")
        if is_gravity(joint):
            c.execute("UPDATE current_state SET status='unknown', detail='offline', angle_deg=NULL, confidence=0.0,"
                      " energized=0, last_write_at=? WHERE joint=?", (now, joint))
            _log(c, "switch_off", joint=joint, reason=reason, gravity=True, became="unknown")
            def mut(rec):
                for jt in rec["joint_vector"]:
                    if jt["name"] == joint:
                        jt.update(status="unknown", q_deg=None, provenance="unknown",
                                  confidence=0.0, energized=False)
                _set_fault_digits(rec, joint, "4")
            pid = _repose(c, mutate=mut)
        else:
            c.execute("UPDATE current_state SET status='off', detail='offline', angle_deg=NULL, confidence=0.0,"
                      " energized=0, last_write_at=? WHERE joint=?", (now, joint))
            _log(c, "switch_off", joint=joint, reason=reason, gravity=False)
            pid = _repose(c, mutate=lambda rec: _apply_fault(rec, joint, "offline"))
        self._commit()
        return pid

    def switch_on(self, joint, reason="operator", now=None):
        """Re-energise an intentionally-off (status='off') joint. Returns as unknown until rehomed."""
        now = self.clock() if now is None else now
        c = self.con
        r = c.execute("SELECT status FROM current_state WHERE joint=?", (joint,)).fetchone()
        if r is None or r[0] != "off":
            raise ValueError(f"cannot switch on joint that is not off: {joint}")
        c.execute("UPDATE current_state SET status='unknown', detail='unknown', angle_deg=NULL, confidence=0.0,"
                  " energized=0, last_write_at=? WHERE joint=?", (now, joint))
        _log(c, "switch_on", joint=joint, reason=reason)
        def mut(rec):
            for jt in rec["joint_vector"]:
                if jt["name"] == joint:
                    jt.update(status="unknown", q_deg=None, provenance="unknown",
                              confidence=0.0, energized=False)
            _set_fault_digits(rec, joint, "4")
        pid = _repose(c, mutate=mut)
        self._commit()
        return pid

    def mark_unknown(self, joints=None, reason="autoDisable", now=None):
        """autoDisable / power cut -> unknown (q=null, conf 0) until rehome (OPEN-W24).
        joints=None means all present joints (power cut)."""
        now = self.clock() if now is None else now
        c = self.con
        if joints is None:
            joints = [r[0] for r in c.execute("SELECT joint FROM current_state WHERE status!='absent'")]
        for jn in joints:
            c.execute("UPDATE current_state SET status=CASE WHEN status='fault' THEN 'fault' ELSE 'unknown' END,"
                      " angle_deg=CASE WHEN status='fault' THEN angle_deg ELSE NULL END,"
                      " confidence=0.0, energized=0, last_write_at=? WHERE joint=? AND status!='absent'", (now, jn))
            _log(c, "mark_unknown", joint=jn, reason=reason)
        faulted = {r[0] for r in c.execute("SELECT joint FROM current_state WHERE status='fault'")}
        def mut(rec):
            for jt in rec["joint_vector"]:
                if jt["name"] in joints and jt["name"] not in faulted and jt["status"] != "absent":
                    jt.update(status="unknown", q_deg=None, provenance="unknown", confidence=0.0, energized=False)
                    _set_fault_digits(rec, jt["name"], "4")  # spec §5.1
        all_unknown = c.execute("SELECT COUNT(*) FROM current_state WHERE status IN ('ok','fault','off')").fetchone()[0] == 0
        if reason == "power_cut" or all_unknown:
            # §9 #4: no live joint -> the pose is unknown, never the old library name
            def mut2(rec, _m=mut):
                _m(rec); rec["pose_name"] = "unknown"
            pid = _repose(c, "unknown", mutate=mut2)
        else:
            pid = _repose(c, mutate=mut)
        if reason == "power_cut":
            c.execute("INSERT OR REPLACE INTO meta VALUES ('rehome_required','1')")
        self._commit()
        return pid

    def raise_fault(self, joint, kind="stuck", detail=None, now=None):
        if kind == "offline":  # digit 5 = commanded off, intentional (§9 #7)
            return self.switch_off(joint, reason=detail or "commanded offline", now=now)
        now = self.clock() if now is None else now
        c = self.con
        r = c.execute("SELECT status FROM current_state WHERE joint=?", (joint,)).fetchone()
        if r is None or r[0] == "absent":
            raise ValueError(f"cannot fault absent/unknown channel {joint}")
        c.execute("UPDATE current_state SET status='fault', detail=?, last_write_at=? WHERE joint=?",
                  (kind, now, joint))
        c.execute("INSERT INTO faults(joint,kind,detail,raised_at) VALUES (?,?,?,?)", (joint, kind, detail, now))
        _log(c, "fault_raised", joint=joint, fault_kind=kind, detail=detail)
        pid = _repose(c, mutate=lambda rec: _apply_fault(rec, joint, kind))
        self._commit()
        return pid

    def rehome(self, pose_id, now=None):
        """Simulated supervised slow homing move to a library pose.
        Faulted joints stay faulted. Intentionally-off joints stay off (only switch_on clears them).
        Does not clear a latched e-stop (OPEN-16a)."""
        now = self.clock() if now is None else now
        c = self.con
        row = c.execute("SELECT record_json FROM pose_library WHERE pose_id=?", (pose_id,)).fetchone()
        if not row:
            raise KeyError(pose_id)
        rec = json.loads(row[0])
        faulted = {r[0]: r[1:] for r in c.execute(
            "SELECT joint, detail, angle_deg FROM current_state WHERE status='fault'")}
        offed = {r[0]: r[1] for r in c.execute(
            "SELECT joint, detail FROM current_state WHERE status='off'")}
        self.update_from_sim(rec, now=now, verified=True)
        for jn, (det, ang) in faulted.items():
            c.execute("UPDATE current_state SET status='fault', detail=?, angle_deg=? WHERE joint=?", (det, ang, jn))
        for jn, det in offed.items():
            c.execute("UPDATE current_state SET status='off', detail=?, angle_deg=NULL, confidence=0.0,"
                      " energized=0 WHERE joint=?", (det or "offline", jn))
        c.execute("DELETE FROM meta WHERE key='rehome_required'")
        if faulted or offed:
            def mut(r):
                for jn, (det, ang) in faulted.items():
                    _apply_fault(r, jn, det or "fault")
                for jn, det in offed.items():
                    _apply_fault(r, jn, "offline")
            _repose(c, rec["pose_name"], mutate=mut)
        _log(c, "rehome", pose_id=pose_id, kept_faults=sorted(faulted), kept_off=sorted(offed))
        self._commit()

    def _commit(self):
        """Commit, then (explicitly) refresh the NOVA snapshot so NOVA never needs the live db."""
        self.con.commit()
        if self.auto_snapshot:
            self.write_snapshot()

    def write_snapshot(self, dst=None):
        """Operator/writer side (OPEN-18): copy the store to a standalone non-WAL snapshot that
        NOVA reads with mode=ro&immutable=1. dst defaults to self.snapshot_path (config nova_snapshot_path). Atomic replace."""
        import os, sqlite3
        from pathlib import Path
        from config import load_config
        dst = Path(dst or self.snapshot_path or load_config()["nova_snapshot_path"])
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".tmp")
        if tmp.exists(): tmp.unlink()
        out = sqlite3.connect(tmp)
        try:
            self.con.backup(out)
            out.execute("PRAGMA journal_mode=DELETE")
        finally:
            out.close()
        os.replace(tmp, dst)
        return str(dst)

    def current_record(self):
        """The full bod.pose.v1 record behind the current pose_id (canonically re-minted)."""
        return _get_rec(self.con)

    def purge_events(self, cfg_days=None):
        """Retention PLACEHOLDER: no-op unless events_retention_days is configured."""
        if cfg_days is None:
            return 0
        c = self.con
        c.execute("INSERT OR REPLACE INTO meta VALUES ('retention_purge','1')")
        n = c.execute("DELETE FROM events WHERE ts < ?", (self.clock() - cfg_days * 86400,)).rowcount
        c.execute("DELETE FROM meta WHERE key='retention_purge'")
        self._commit()
        return n
