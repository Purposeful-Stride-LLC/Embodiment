"""Register -> calibration_limits. Measured values (measured.json) override mrl defaults.

OPEN-17: loading is a pure read (no file writes). Logging overrides is an explicit
operator step — see calibration/apply_overrides.py. SIM ONLY.
"""
import json
from pathlib import Path

LIMIT_FIELDS = ("min", "max", "rest")


class MeasuredError(ValueError):
    """measured.json is malformed (e.g. duplicate keys, OPEN-18c)."""


def _no_dupes(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise MeasuredError(f"duplicate key {k!r}")  # file name added once by the caller
        seen[k] = v
    return seen


def _load(p, strict=False):
    if p is None:
        return {}
    p = Path(p)
    if not p.exists():
        return {}
    if not strict:
        return json.loads(p.read_text())
    try:
        return json.loads(p.read_text(), object_pairs_hook=_no_dupes)
    except MeasuredError as e:
        raise MeasuredError(f"{e} ({p.name})") from None


def _fingerprint(ev):
    """Stable identity of a measured override (excludes log-only fields)."""
    return json.dumps(
        {"calibration_id": ev["calibration_id"], "joint": ev["joint"], "measured": ev["measured"]},
        sort_keys=True, separators=(",", ":"),
    )


def compute(register_path, measured_path):
    """Pure compute. Returns (calibration_limits, effective entries, override events).
    Never opens a file for writing."""
    reg = _load(register_path)
    meas = _load(measured_path, strict=True)
    absent = set(reg.get("always_absent", []))
    limits, entries, events = {}, {}, []
    for cal, block in reg.get("calibrations", {}).items():
        ent = {j: dict(e) for j, e in block.get("joints", {}).items() if j not in absent}
        for j, m in (meas.get(cal) or {}).items():
            if j.startswith("_"):
                continue
            ev = {"calibration_id": cal, "joint": j, "measured": m}
            vals = {k: m[k] for k in LIMIT_FIELDS if k in m}
            if j in absent:
                ev.update(action="rejected", reason="joint must stay absent (OPEN-W9)")
            elif j not in ent:
                ev.update(action="rejected", reason="no register entry for joint")
            elif not vals or not all(isinstance(v, (int, float)) for v in vals.values()):
                ev.update(action="rejected", reason="no numeric min/max/rest")
            else:
                new = {**{k: ent[j][k] for k in LIMIT_FIELDS}, **vals}
                if not (new["min"] <= new["rest"] <= new["max"]):
                    ev.update(action="rejected", reason=f"inconsistent min/rest/max {new}")
                else:
                    ev.update(action="applied", default={k: ent[j][k] for k in LIMIT_FIELDS},
                              effective=new)
                    ent[j].update(
                        new,
                        source={"kind": "measured", "measured_by": m.get("measured_by"),
                                "date": m.get("date"), "default_source": ent[j]["source"]},
                        verified=bool(m.get("verified", False)),
                    )
            events.append(ev)
        limits[cal] = {j: [e["min"], e["max"]] for j, e in ent.items()}
        entries[cal] = ent
    return limits, entries, events


def logged_fingerprints(log_path):
    """Set of fingerprints already present in overrides.log (read-only)."""
    log_path = Path(log_path) if log_path else None
    if not log_path or not log_path.exists():
        return set()
    out = set()
    for line in log_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "calibration_id" in rec and "joint" in rec and "measured" in rec:
            out.add(_fingerprint(rec))
    return out


def count_unapplied(events, log_path):
    """How many computed override events are not yet present in the log."""
    logged = logged_fingerprints(log_path)
    return sum(1 for ev in events if _fingerprint(ev) not in logged)


def effective(register_path, measured_path, log_path=None):
    """Pure read (OPEN-17). Returns (limits, entries, events). Never writes.
    log_path is accepted for API compatibility but is never opened for writing.
    """
    return compute(register_path, measured_path)
