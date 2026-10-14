"""Operator-only controls for the BOD store (sim).

Module name: estop_release (not stdlib operator). NEVER import from rag_adapter.py or triggers.py. E-stop release is
unreachable from the read-only adapter and from trigger detection; only an
explicit operator call with id + reason may clear a latched e-stop.
"""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from init_db import connect_rw
from store import _log

_CDT = ZoneInfo("America/Chicago")


def _cdt_stamp(ts=None):
    """Format a real-clock instant as America/Chicago with a CDT/CST tag."""
    dt = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=_CDT)
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def _refresh_snapshot(con):
    """OPEN-18a: operator write => refresh the NOVA snapshot (NOVA never reads the live db)."""
    import os, sqlite3
    from pathlib import Path
    from config import load_config
    p = load_config().get("nova_snapshot_path")
    if not p:
        return
    dst = Path(p); dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp"); tmp.unlink(missing_ok=True)
    out = sqlite3.connect(tmp)
    try:
        con.backup(out); out.execute("PRAGMA journal_mode=DELETE")
    finally:
        out.close()
    os.replace(tmp, dst)


def release_estop(operator_id, reason, db_path=None):
    """Clear a latched e-stop. Requires operator_id and reason; appends estop_audit.

    Timestamps use the real wall clock at write time (not the sim clock).
    Does not set motion_allowed; the operator must re-enable motion separately.
    """
    if not operator_id or not str(operator_id).strip():
        raise ValueError("operator_id required to release e-stop")
    if not reason or not str(reason).strip():
        raise ValueError("reason required to release e-stop")
    ts = time.time()
    when_cdt = _cdt_stamp(ts)
    con = connect_rw(db_path)
    try:
        row = con.execute("SELECT value FROM meta WHERE key='estop'").fetchone()
        if not row or row[0] != "active":
            raise ValueError("e-stop is not active")
        con.execute("INSERT OR REPLACE INTO meta VALUES ('estop', 'released')")
        con.execute(
            "INSERT INTO estop_audit(ts, when_cdt, operator_id, reason) VALUES (?,?,?,?)",
            (ts, when_cdt, str(operator_id).strip(), str(reason).strip()),
        )
        _log(con, "estop_released", operator_id=str(operator_id).strip(),
             reason=str(reason).strip(), when_cdt=when_cdt)
        con.commit()
        _refresh_snapshot(con)
        return {"when_cdt": when_cdt, "operator_id": str(operator_id).strip(),
                "reason": str(reason).strip()}
    finally:
        con.close()
