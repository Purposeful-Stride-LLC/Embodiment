"""Operator-only: validate measured.json and append override events to overrides.log.

NEVER import from rag_adapter.py or triggers.py. Logging is unreachable from the
read-only adapter and from trigger detection; only an explicit operator/store
call may write overrides.log. Each apply logs every current measured override
with a fresh event_id — repeated applies are each logged (no dedupe). SIM ONLY.

Run: `python calibration/apply_overrides.py` (direct script; works from any cwd) or
`python -m calibration.apply_overrides` (must be run from bod_store/, or with PYTHONPATH=<bod_store>).
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# OPEN-18b: works as `python calibration/apply_overrides.py` and `python -m calibration.apply_overrides`
_HOME = str(Path(__file__).resolve().parents[1])
if _HOME not in sys.path:
    sys.path.insert(0, _HOME)

from calibration.loader import compute, MeasuredError  # noqa: E402

_CDT = ZoneInfo("America/Chicago")


def now_cdt():
    """Zone-aware: real clock converted to America/Chicago (CDT/CST tag)."""
    return datetime.fromtimestamp(time.time(), tz=_CDT).strftime("%Y-%m-%d %H:%M:%S %Z")


def apply_overrides(register_path=None, measured_path=None, log_path=None):
    """Validate measured.json against the register and append every override event.

    Each event gets a unique event_id. Calling this again with the same measured.json
    appends a fresh set of events (no dedupe). Returns the list of logged records.
    """
    if register_path is None or measured_path is None or log_path is None:
        from config import load_config
        # load_config is pure-read; pull default paths then apply
        cfg = load_config()
        register_path = register_path or cfg["calibration_register"]
        measured_path = measured_path or cfg["calibration_measured"]
        log_path = log_path or cfg["calibration_override_log"]
    _, _, events = compute(register_path, measured_path)
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logged = []
    with log_path.open("a") as f:
        for ev in events:
            rec = {
                "event_id": uuid.uuid4().hex,
                "logged_at": now_cdt(),
                **ev,
            }
            f.write(json.dumps(rec, sort_keys=True) + "\n")
            logged.append(rec)
    return logged


def main():
    try:
        rows = apply_overrides()
    except MeasuredError as e:  # OPEN-18c: refuse, nothing written
        print(f"REFUSED: {e}; nothing logged", file=sys.stderr)
        return 2
    print(f"logged {len(rows)} override event(s) -> calibration/overrides.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
