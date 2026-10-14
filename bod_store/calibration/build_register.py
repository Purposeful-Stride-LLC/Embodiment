"""Builds register.json from real MRL/InMoov2 default configs (read-only sources). SIM ONLY.
Every value is parsed from a source line; nothing is typed in by hand.
Sources:
  FS  = MyRobotLab/inmoov2 @5715ec6b system/startScripts/InMoov2FullStart.py (local clone under $BOD_MRL_ROOT/inmoov2)
  FST = MyRobotLab/inmoov  @c450e19 InMoov/testScripts/InMoov.minimalFingerStarter.py (local clone under $BOD_MRL_ROOT/inmoov)
  JAVA= MyRobotLab/myrobotlab @c86c2897 service/config/InMoov2{Arm,Hand}Config.java (cross-check only; optional copy in calibration/sources/, not distributed)
"""
import json, os, re, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
FS_SHA = "5715ec6b5c0a64e8f440735332c2ca621a93fd1c"
FST_SHA = "c450e1945f45e2b48b6c3f2b4ab39501e21f5c78"
MRL_SHA = "c86c289751363a52ba204b5f7359c55af40d373a"
MRL_ROOT = Path(os.environ.get("BOD_MRL_ROOT") or HERE.parents[1] / "upstream")
FS_REL = Path("inmoov2/system/startScripts/InMoov2FullStart.py")
FST_REL = Path("inmoov/InMoov/testScripts/InMoov.minimalFingerStarter.py")
FS_PATH, FST_PATH = MRL_ROOT / FS_REL, MRL_ROOT / FST_REL
FS_URL = f"https://github.com/MyRobotLab/inmoov2/blob/{FS_SHA}/system/startScripts/InMoov2FullStart.py"
FST_URL = f"https://github.com/MyRobotLab/inmoov/blob/{FST_SHA}/InMoov/testScripts/InMoov.minimalFingerStarter.py"
JAVA_URL = f"https://github.com/MyRobotLab/myrobotlab/blob/{MRL_SHA}/src/main/java/org/myrobotlab/service/config/"
ABSENT = ("head.rollNeck", "torso.midStom")
HAND = ["thumb", "index", "majeure", "ringFinger", "pinky", "wrist"]
ARM = ["bicep", "rotate", "shoulder", "omoplate"]
RIGHT = [f"rightHand.{x}" for x in HAND] + [f"rightArm.{x}" for x in ARM]
CLASSIC = RIGHT + [f"leftHand.{x}" for x in HAND] + [f"leftArm.{x}" for x in ARM] + \
    ["head.neck", "head.rothead", "head.jaw", "head.eyeX", "head.eyeY", "torso.topStom"]
NUM = r"(-?\d+(?:\.\d+)?)"

def num(s):
    f = float(s); return int(f) if f.is_integer() else f

def parse(path, prefix):
    out = {}
    for i, line in enumerate(path.read_text().splitlines(), 1):
        m = re.match(rf"\s*{prefix}(\w+)_(\w+)\.map\({NUM},\s*{NUM},\s*{NUM},\s*{NUM}\)", line) if prefix == "i01_" else \
            re.match(rf"\s*(rightHand)\.(\w+)\.map\({NUM},\s*{NUM},\s*{NUM},\s*{NUM}\)", line)
        if m:
            out.setdefault(f"{m[1]}.{m[2]}", {}).update(map_line=i, minIn=num(m[3]), maxIn=num(m[4]), minOut=num(m[5]), maxOut=num(m[6]))
        m = re.match(rf"\s*{prefix}(\w+)_(\w+)\.setRest\({NUM}\)", line) if prefix == "i01_" else \
            re.match(rf"\s*(rightHand)\.(\w+)\.setRest\({NUM}\)", line)
        if m:
            out.setdefault(f"{m[1]}.{m[2]}", {}).update(rest_line=i, rest=num(m[3]))
    return out

def java_rest(cls, joint):
    p = HERE / "sources" / f"{cls}.java"
    if not p.exists(): return None, None  # cross-check skipped when sources/ absent
    for i, line in enumerate(p.read_text().splitlines(), 1):
        m = re.match(rf"\s*{joint}\.rest\s*=\s*{NUM};", line)
        if m: return num(m[1]), i
    return None, None

def notes_for(name, d):
    n = []
    if name in ("head.eyeX", "head.eyeY"):
        n.append(f"OPEN-W9: eyeX/eyeY output range conflict; this source maps output {d['minOut']}-{d['maxOut']}. Input limits used as-is.")
    if name.startswith(("rightHand.", "leftHand.")) and not name.endswith("wrist"):
        jr, jl = java_rest("InMoov2HandConfig", name.split(".")[1])
        if jr is not None and jr != d["rest"]:
            n.append(f"Source conflict (not W9/W10): InMoov2FullStart.py rest={d['rest']} vs MRL InMoov2HandConfig.java rest={jr} ({JAVA_URL}InMoov2HandConfig.java#L{jl}). FullStart value used.")
    if name.endswith("wrist"):
        jr, jl = java_rest("InMoov2HandConfig", "wrist")
        if jr is not None and jr != d["rest"]:
            n.append(f"Source conflict (not W9/W10): FullStart rest={d['rest']} vs InMoov2HandConfig.java rest={jr} ({JAVA_URL}InMoov2HandConfig.java#L{jl}). FullStart value used.")
    return n

def entry(name, d, url, path):
    return {"min": d["minIn"], "max": d["maxIn"], "rest": d["rest"],
            "out_min": d["minOut"], "out_max": d["maxOut"],
            "source": {"kind": "mrl_default", "url": f"{url}#L{d['map_line']}", "path": str(path.relative_to(MRL_ROOT)),
                       "map_line": d["map_line"], "rest_line": d["rest_line"]},
            "verified": False, "notes": notes_for(name, d) if url == FS_URL else []}

def build():
    fs = parse(FS_PATH, "i01_"); fst = parse(FST_PATH, None)
    def block(joints):
        return {j: entry(j, fs[j], FS_URL, FS_PATH) for j in joints}
    absent = {
        "head.rollNeck": {"source": {"kind": "mrl_default", "url": f"{FS_URL}#L{fs['head.rollNeck']['map_line']}", "path": str(FS_REL)},
                          "notes": ["OPEN-W9: rollNeck output map 60-130 (this file) vs 20-170 (dossier legacy config); pin 12 vs 30 (OPEN-Q19). Kept ABSENT; no limits issued."]},
        "torso.midStom": {"source": {"kind": "mrl_default", "url": f"{FS_URL}#L{fs['torso.midStom']['map_line']}", "path": str(FS_REL)},
                          "notes": ["OPEN-W9: midStom input 0-180 (this file) vs 60-120 (spec). Kept ABSENT; no limits issued."]}}
    excluded = {"torso.lowStom": {"source": {"kind": "mrl_default", "url": f"{FS_URL}#L{fs['torso.lowStom']['map_line']}", "path": str(FS_REL)},
                                  "notes": ["OPEN-W10: lowStom servo in Nixie script (pin 29) but dossier says no low-stomach servo. Excluded from classic31_v1."]}}
    fsj = fst["rightHand.index"]
    fs_e = entry("rightHand.index", fsj, FST_URL, FST_PATH)
    fs_e["notes"] = ["Legacy InMoov test script (pre-Nixie); output map 42-160 matches calibration_id. setRest(0)."]
    return {
        "_comment": "BOD calibration register DRAFT (SIM ONLY). All values mrl_default, verified=false. Generated by build_register.py; do not hand-edit. Measured overrides go in measured.json.",
        "register_version": "draft-2026-10-13",
        "signed": False,
        "sources": {"inmoov2_fullstart": FS_URL, "inmoov_minimalFingerStarter": FST_URL,
                    "mrl_InMoov2HandConfig": JAVA_URL + "InMoov2HandConfig.java", "mrl_InMoov2ArmConfig": JAVA_URL + "InMoov2ArmConfig.java"},
        "always_absent": list(ABSENT),
        "calibrations": {
            "cal-fingerstarter-testscript-map-0-180-42-160": {"body_profile": "finger_starter_v1", "joints": {"rightHand.index": fs_e}},
            "cal-rightarm-nixie-fullstart-defaults-v0": {"body_profile": "right_arm_v1", "joints": block(RIGHT)},
            "cal-classic31-nixie-fullstart-defaults-v0": {"body_profile": "classic31_v1", "joints": block(CLASSIC),
                                                          "absent": absent, "excluded": excluded}}}

if __name__ == "__main__":
    reg = build()
    (HERE / "register.json").write_text(json.dumps(reg, indent=1) + "\n")
    print("wrote register.json")
