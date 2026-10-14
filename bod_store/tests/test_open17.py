"""OPEN-17: read-only adapter/triggers must not write calibration/overrides.log.
Pure read vs explicit apply_overrides; repeated applies each logged. SIM ONLY."""
import builtins
import hashlib
import json
import os
from pathlib import Path

import pytest

from calibration.apply_overrides import apply_overrides
from calibration.loader import compute, count_unapplied, effective
from config import load_config
from rag_adapter import body_status, body_status_block, body_status_json, connect_ro
from triggers import detect, fault_scope, goal_checks

HOME = Path(__file__).resolve().parents[1]
REG = HOME / "calibration" / "register.json"
E8_CAL = "cal-rightarm-nixie-fullstart-defaults-v0"
MEAS = {E8_CAL: {"rightArm.bicep": {"min": 5, "max": 80, "rest": 5,
                                     "measured_by": "bench", "date": "2026-10-13"}}}


def _meas(tmp_path, data=None):
    m = tmp_path / "measured.json"
    m.write_text(json.dumps(data if data is not None else MEAS))
    return m


def _cfg_with(tmp_path, measured, log=None):
    cfg = json.loads((HOME / "bod_config.json").read_text())
    cfg.update(calibration_register=str(REG), calibration_measured=str(measured),
               calibration_override_log=str(log or (tmp_path / "ov.log")))
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(cfg))
    return p


def _snapshot(root: Path):
    """mtime + sha256 of every regular file under root (skip .venv, __pycache__, .pytest_cache)."""
    skip = {".venv", "__pycache__", ".pytest_cache"}
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for name in filenames:
            p = Path(dirpath) / name
            if not p.is_file() or p.is_symlink():
                continue
            st = p.stat()
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            out[str(p.relative_to(root))] = (st.st_mtime_ns, h)
    return out


def test_effective_is_pure_read(tmp_path):
    m = _meas(tmp_path)
    log = tmp_path / "ov.log"
    lim, ent, ev = effective(REG, m, log)
    assert lim[E8_CAL]["rightArm.bicep"] == [5, 80]
    assert ent[E8_CAL]["rightArm.bicep"]["source"]["kind"] == "measured"
    assert len(ev) == 1 and ev[0]["action"] == "applied"
    assert not log.exists()


def test_repeated_applies_each_logged(tmp_path):
    m = _meas(tmp_path)
    log = tmp_path / "ov.log"
    r1 = apply_overrides(REG, m, log)
    r2 = apply_overrides(REG, m, log)
    rows = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    assert len(r1) == 1 and len(r2) == 1 and len(rows) == 2
    assert rows[0]["event_id"] != rows[1]["event_id"]
    assert all(r["action"] == "applied" and "event_id" in r for r in rows)
    assert all(r["logged_at"].endswith((" CDT", " CST")) for r in rows)
    # after apply, unapplied count is 0
    _, _, ev = compute(REG, m)
    assert count_unapplied(ev, log) == 0


def test_unapplied_flagged_in_status(env, tmp_path, monkeypatch):
    m = _meas(tmp_path)
    log = tmp_path / "ov.log"
    monkeypatch.setenv("BOD_CONFIG", str(_cfg_with(tmp_path, m, log)))
    env["st"].rehome(env["recs"][0]["pose_id"])
    s = body_status(now=1000)
    assert s["calibration_unapplied"] == 1
    block = body_status_block(now=1000)
    assert "calibration: 1 unapplied overrides" in block
    assert not log.exists()
    apply_overrides(REG, m, log)
    s2 = body_status(now=1000)
    assert s2["calibration_unapplied"] == 0
    assert "unapplied overrides" not in body_status_block(now=1000)


def test_readers_do_not_write_with_measured(env, tmp_path, monkeypatch):
    """Fill measured.json, call every rag_adapter + triggers entry point;
    no file under bod_store changes; nothing opened for writing."""
    # Point config at a measured file that lives OUTSIDE bod_store so writing
    # measured itself isn't part of the fixture; the install measured stays empty.
    m = _meas(tmp_path)
    log = HOME / "calibration" / "overrides.log"
    if log.exists():
        log.unlink()
    monkeypatch.setenv("BOD_CONFIG", str(_cfg_with(tmp_path, m, log)))

    before = _snapshot(HOME)

    write_opens = []
    real_open = builtins.open

    def tracking_open(file, mode="r", *a, **kw):
        mode_s = mode if isinstance(mode, str) else str(mode)
        if any(c in mode_s for c in "wxa+"):
            # '+' with 'r' is read/write; treat any write-capable mode as a write
            if "r" in mode_s and "+" not in mode_s:
                pass
            else:
                write_opens.append((str(file), mode_s))
        return real_open(file, mode, *a, **kw)

    # Also catch os.open with write flags
    real_os_open = os.open
    os_write_opens = []

    def tracking_os_open(path, flags, *a, **kw):
        write_bits = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
        if flags & write_bits:
            os_write_opens.append((str(path), flags))
        return real_os_open(path, flags, *a, **kw)

    monkeypatch.setattr(builtins, "open", tracking_open)
    monkeypatch.setattr(os, "open", tracking_os_open)

    # --- every rag_adapter entry point ---
    connect_ro(env["db"]).close()
    body_status(db_path=env["db"], now=1000)
    body_status_block(db_path=env["db"], now=1000)
    body_status_json(db_path=env["db"], now=1000)

    # --- every triggers entry point ---
    fault_scope([], False)
    fault_scope([("head.jaw", "stall")], False)
    s = body_status(db_path=env["db"], now=1000)
    goal_checks({"pose_id": env["recs"][0]["pose_id"]}, s, db_path=env["db"], now=1000)
    detect(db_path=env["db"], now=1000)
    detect(db_path=env["db"], now=1000, goal={"pose_id": env["recs"][0]["pose_id"]})
    load_config()  # also a reader path used by both

    after = _snapshot(HOME)
    assert before == after, (
        "bod_store files changed during reader calls: "
        + ", ".join(k for k in set(before) | set(after)
                    if before.get(k) != after.get(k))
    )
    # Filter write opens to paths under bod_store (sqlite may use tempfile elsewhere)
    bod_writes = [w for w in write_opens if str(HOME) in w[0] or w[0].startswith(str(HOME))]
    bod_os = [w for w in os_write_opens if str(HOME) in w[0] or w[0].startswith(str(HOME))]
    assert bod_writes == [], f"builtins.open write under bod_store: {bod_writes}"
    assert bod_os == [], f"os.open write under bod_store: {bod_os}"
    assert not log.exists()


def test_adapter_and_triggers_do_not_import_apply():
    import ast
    forbidden_mods = {"apply_overrides"}
    forbidden_names = {"apply_overrides"}
    for fname in ("rag_adapter.py", "triggers.py", "config.py"):
        src = (HOME / fname).read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert a.name.split(".")[0] not in forbidden_mods and "apply_overrides" not in a.name, fname
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "apply_overrides" not in mod, fname
                for a in node.names:
                    assert a.name not in forbidden_names, (fname, a.name)
