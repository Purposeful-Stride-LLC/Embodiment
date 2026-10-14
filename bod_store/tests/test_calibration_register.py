"""Calibration register draft (SIM ONLY): provenance, wiring, measured overrides, E7/E8 goal pass."""
import copy, json, os, re
from pathlib import Path
import pytest
from calibration.loader import effective
from triggers import detect

HOME = Path(__file__).resolve().parents[1]
REG = HOME / "calibration" / "register.json"
E7_CAL = "cal-fingerstarter-testscript-map-0-180-42-160"
E8_CAL = "cal-rightarm-nixie-fullstart-defaults-v0"
C31 = "cal-classic31-nixie-fullstart-defaults-v0"
# Optional upstream check: set BOD_MRL_ROOT to a dir holding clones of MyRobotLab/inmoov2 and MyRobotLab/inmoov
# (at the commits cited in register.json). Default: ../upstream next to the repo. Skipped when absent.
MRL_ROOT = Path(os.environ.get("BOD_MRL_ROOT") or HOME.parent / "upstream")
LOCAL = {"InMoov2FullStart.py": MRL_ROOT / "inmoov2/system/startScripts/InMoov2FullStart.py",
         "InMoov.minimalFingerStarter.py": MRL_ROOT / "inmoov/InMoov/testScripts/InMoov.minimalFingerStarter.py"}

@pytest.fixture(scope="module")
def reg(): return json.loads(REG.read_text())

def _all(reg):
    for cal, b in reg["calibrations"].items():
        for j, e in b["joints"].items(): yield cal, j, e

def _goal(pid, now=1000):
    return {t["trigger_event"]: t for t in detect(now=now, goal={"pose_id": pid})}["goal_requested"]

def test_every_entry_has_required_fields(reg):
    for cal, j, e in _all(reg):
        assert all(isinstance(e[k], (int, float)) for k in ("min", "max", "rest")), (cal, j)
        assert e["min"] <= e["rest"] <= e["max"], (cal, j)
        assert e["verified"] is False
        assert e["source"]["kind"] == "mrl_default"
        assert re.match(r"https://github.com/MyRobotLab/.+/blob/[0-9a-f]{40}/.+#L\d+$", e["source"]["url"])
        assert isinstance(e["notes"], list)

def test_values_match_source_lines(reg):
    """Never invent a value: re-read the cited line and compare."""
    for cal, j, e in _all(reg):
        src = LOCAL[Path(e["source"]["path"]).name]
        if not src.exists(): pytest.skip("local MRL clone not present")
        lines = src.read_text().splitlines()
        m = re.search(r"\.map\(([-\d.]+),\s*([-\d.]+),", lines[e["source"]["map_line"] - 1])
        r = re.search(r"\.setRest\(([-\d.]+)\)", lines[e["source"]["rest_line"] - 1])
        part = j.split(".")[1]
        assert part in lines[e["source"]["map_line"] - 1] and part in lines[e["source"]["rest_line"] - 1]
        assert (float(m[1]), float(m[2]), float(r[1])) == (e["min"], e["max"], e["rest"]), (cal, j)

def test_rollneck_midstom_absent(reg):
    for cal, j, e in _all(reg):
        assert j not in ("head.rollNeck", "torso.midStom")
    c = reg["calibrations"][C31]
    assert set(c["absent"]) == {"head.rollNeck", "torso.midStom"}
    assert all("OPEN-W9" in c["absent"][k]["notes"][0] for k in c["absent"])
    assert "OPEN-W10" in c["excluded"]["torso.lowStom"]["notes"][0]

def test_w9_notes_on_eyes(reg):
    for j in ("head.eyeX", "head.eyeY"):
        assert any("OPEN-W9" in n for n in reg["calibrations"][C31]["joints"][j]["notes"])

def test_e7_e8_coverage(reg, env):
    assert set(reg["calibrations"][E7_CAL]["joints"]) == {"rightHand.index"}
    e8 = {j["name"] for j in env["recs"][7]["joint_vector"]}
    assert set(reg["calibrations"][E8_CAL]["joints"]) == e8

def test_config_limits_from_register(env):
    from config import load_config
    lim = load_config()["calibration_limits"]
    assert lim[E7_CAL] == {"rightHand.index": [0, 180]}
    assert lim[E8_CAL]["rightArm.bicep"] == [0, 90] and lim[E8_CAL]["rightArm.omoplate"] == [10, 80]
    assert len(lim[C31]) == 26 and "head.rollNeck" not in lim[C31] and "torso.midStom" not in lim[C31]

@pytest.mark.parametrize("idx", [6, 7])
def test_e7_e8_goal_pass_clean_state(env, idx):
    pid = env["recs"][idx]["pose_id"]
    env["st"].rehome(pid)
    g = _goal(pid)
    assert g["safe_action"] == "PASS_TO_VALIDATOR" and g["failed_checks"] == []

def test_e7_refused_when_dirty(env):
    pid = env["recs"][6]["pose_id"]
    env["st"].rehome(pid); env["st"].set_estop(True)
    assert _goal(pid)["safe_action"] == "REFUSE_MOVE"

def _meas(tmp_path, data):
    m = tmp_path / "measured.json"; m.write_text(json.dumps(data)); return m

def test_measured_overrides_default_and_logged(tmp_path):
    from calibration.apply_overrides import apply_overrides
    m = _meas(tmp_path, {E8_CAL: {"rightArm.bicep": {"min": 5, "max": 80, "rest": 5, "measured_by": "bench", "date": "2026-10-13"}}})
    log = tmp_path / "ov.log"
    lim, ent, ev = effective(REG, m, log)           # pure read: no log write
    assert lim[E8_CAL]["rightArm.bicep"] == [5, 80]
    assert ent[E8_CAL]["rightArm.bicep"]["source"]["kind"] == "measured"
    assert ent[E8_CAL]["rightArm.bicep"]["source"]["default_source"]["kind"] == "mrl_default"
    assert lim[C31]["rightArm.bicep"] == [0, 90]   # other calibration untouched
    assert not log.exists()
    apply_overrides(REG, m, log)
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["action"] == "applied" and rows[0]["default"]["max"] == 90
    assert "event_id" in rows[0]
    apply_overrides(REG, m, log)                    # reload: each apply logged (no dedupe)
    assert len(log.read_text().splitlines()) == 2

def test_measured_override_rejections_logged(tmp_path):
    from calibration.apply_overrides import apply_overrides
    m = _meas(tmp_path, {C31: {"head.rollNeck": {"min": 60, "max": 130, "rest": 90},
                               "head.neck": {"min": 100, "max": 50},
                               "torso.lowStom": {"min": 0, "max": 180}}})
    log = tmp_path / "ov.log"
    lim, _, ev = effective(REG, m, log)
    assert "head.rollNeck" not in lim[C31] and lim[C31]["head.neck"] == [0, 180]
    assert [e["action"] for e in ev] == ["rejected"] * 3
    assert not log.exists()                         # pure read
    apply_overrides(REG, m, log)
    assert len(log.read_text().splitlines()) == 3

def test_measured_override_reaches_goal_check(env, tmp_path, monkeypatch):
    m = _meas(tmp_path, {E8_CAL: {"rightArm.bicep": {"min": 0, "max": 30, "rest": 0}}})
    monkeypatch.setenv("BOD_CONFIG", str(tmp_path / "cfg.json"))
    cfg = json.loads((HOME / "bod_config.json").read_text())
    cfg.update(calibration_register=str(REG), calibration_measured=str(m), calibration_override_log=str(tmp_path / "ov.log"))
    (tmp_path / "cfg.json").write_text(json.dumps(cfg))
    pid = env["recs"][7]["pose_id"]; env["st"].rehome(pid)
    g = _goal(pid)  # E8 bicep = 45
    assert g["safe_action"] == "REFUSE_MOVE" and g["failed_checks"] == ["rightArm.bicep target 45 outside limits [0, 30]"]
    assert not (tmp_path / "ov.log").exists()        # OPEN-17: reader path does not log

def test_override_log_timestamp_is_chicago(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from calibration.apply_overrides import apply_overrides
    m = _meas(tmp_path, {E8_CAL: {"rightArm.bicep": {"min": 0, "max": 80, "rest": 0}}})
    rows = apply_overrides(REG, m, tmp_path / "ov.log")
    ts = rows[0]["logged_at"]
    assert ts.endswith((" CDT", " CST"))
    exp = datetime.now(ZoneInfo("America/Chicago"))
    got = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("America/Chicago"))
    assert abs((exp - got).total_seconds()) < 60
