"""Path/config resolution. Every path is relative to the install dir (this file's dir)
unless absolute. Env overrides: BOD_HOME, BOD_SNAPSHOT_PATH, BOD_CONFIG, BOD_DB_PATH, BOD_EXAMPLES_PATH, BOD_STALE_S.

OPEN-17: load_config is a pure read — it never writes calibration/overrides.log.
Operator apply is calibration.apply_overrides.apply_overrides.
"""
import json, os
from pathlib import Path

def install_dir() -> Path:
    return Path(os.environ.get("BOD_HOME") or Path(__file__).resolve().parent)

def load_config() -> dict:
    home = install_dir()
    cfg_path = Path(os.environ.get("BOD_CONFIG") or home / "bod_config.json")
    if not cfg_path.is_absolute():
        cfg_path = home / cfg_path
    cfg = {"calibration_register": "./calibration/register.json", "calibration_measured": "./calibration/measured.json",
           "calibration_override_log": "./calibration/overrides.log", "db_path": "./data/bod_state.db", "examples_path": "../bod_pose_state.examples.json",
           "nova_snapshot_path": "./data/nova_snapshot.db", "stale_after_s": 5.0, "events_retention_days": None}
    if cfg_path.exists():
        cfg.update({k: v for k, v in json.loads(cfg_path.read_text()).items() if not k.startswith("_")})
    for env, key in (("BOD_DB_PATH", "db_path"), ("BOD_EXAMPLES_PATH", "examples_path"),
                     ("BOD_SNAPSHOT_PATH", "nova_snapshot_path")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    if os.environ.get("BOD_STALE_S"):
        cfg["stale_after_s"] = float(os.environ["BOD_STALE_S"])
    for key in ("db_path", "examples_path", "calibration_register", "calibration_measured", "calibration_override_log", "nova_snapshot_path"):
        if cfg.get(key):
            p = Path(cfg[key])
            cfg[key] = str(p if p.is_absolute() else (home / p).resolve())
    if cfg.get("calibration_register"):
        from calibration.loader import compute, count_unapplied, MeasuredError
        try:
            limits, entries, events = compute(cfg["calibration_register"], cfg["calibration_measured"])
            cfg["calibration_measured_error"] = None
        except MeasuredError as e:  # OPEN-18c: flag, ignore overrides, never write
            cfg["calibration_measured_error"] = str(e)
            limits, entries, events = compute(cfg["calibration_register"], None)
        cfg["calibration_limits"] = limits
        cfg["calibration_entries"] = entries
        cfg["calibration_overrides"] = events
        cfg["calibration_unapplied"] = count_unapplied(events, cfg.get("calibration_override_log"))
    else:
        cfg["calibration_unapplied"] = 0
    return cfg
