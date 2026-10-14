"""Round-trip: store-minted BP1- ids follow spec §2.8 and pass tools/bod_validate.py's own check()."""
import importlib.util, json, sys
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("bod_validate", ROOT / "tools" / "bod_validate.py")
bv = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(bv)
VAL = Draft202012Validator(json.loads((ROOT / "bod_pose_state.schema.json").read_text()),
                           format_checker=FormatChecker())
import bod_ids

def _valid(rec):
    errs = bv.check(rec, VAL)
    assert not errs, errs
    assert rec["pose_id"] == "BP1-" + bv._h(bv._payload(rec))[:16] == bod_ids.pose_id(rec)

def test_eight_examples_recompute_exactly(env):
    recs = env["recs"]
    assert len(recs) == 8
    for r in recs:
        assert bod_ids.canonical_payload(r) == r["checksums"]["canonical_payload"] == bv._payload(r)
        assert bod_ids.joint_checksum(r) == r["checksums"]["joint_checksum"]
        assert bod_ids.pose_id(r) == r["pose_id"]
        assert bod_ids.remint(r) == r  # incl. record_checksum

def test_spec_worked_example():
    assert bod_ids.sha256_hex('{"cal":"cal-classic31-nixie-fullstart-defaults-v0","fm":"0000000000000000000000660000066","j":{"head.eyeX":90,"head.eyeY":90,"head.jaw":0,"head.neck":90,"head.rollNeck":null,"head.rothead":90,"leftArm.bicep":0,"leftArm.omoplate":10,"leftArm.rotate":90,"leftArm.shoulder":30,"leftHand.index":0,"leftHand.majeure":0,"leftHand.pinky":0,"leftHand.ringFinger":0,"leftHand.thumb":0,"leftHand.wrist":90,"rightArm.bicep":0,"rightArm.omoplate":10,"rightArm.rotate":90,"rightArm.shoulder":30,"rightHand.index":0,"rightHand.majeure":0,"rightHand.pinky":0,"rightHand.ringFinger":0,"rightHand.thumb":0,"rightHand.wrist":90,"torso.midStom":null,"torso.topStom":90},"p":"classic31_v1","q":"grid5","v":"bod.pose.v1"}').startswith("ddcab2230df85dcc")

def test_quantize_rule():
    assert bod_ids.quantize(2) == 0 and bod_ids.quantize(2.5) == 5 and bod_ids.quantize(71) == 70
    assert bod_ids.quantize(183, 5, 0, 180) == 180 and bod_ids.quantize(None) is None

SCENARIOS = {
    "power_cut": lambda st, r: st.mark_unknown(None, reason="power_cut"),
    "autodisable_one": lambda st, r: st.mark_unknown(["leftArm.shoulder"]),
    "stall": lambda st, r: st.raise_fault("rightArm.shoulder", "stall"),
    "weak": lambda st, r: st.raise_fault("rightHand.index", "weak"),
    "offline": lambda st, r: st.raise_fault("head.jaw", "offline"),
    "fault_then_rehome": lambda st, r: (st.raise_fault("rightArm.bicep", "stuck"), st.rehome(r[0]["pose_id"])),
    "unknown_then_rehome": lambda st, r: (st.mark_unknown(None, reason="power_cut"), st.rehome(r[1]["pose_id"])),
}

@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.parametrize("idx", range(6))
def test_store_ids_validate(env, name, idx):
    st, recs = env["st"], env["recs"]
    if name == "offline" and recs[idx]["body_profile"] != "classic31_v1":
        pytest.skip()
    st.update_from_sim(recs[idx])
    _valid(st.current_record())
    SCENARIOS[name](st, recs)
    rec = st.current_record()
    _valid(rec)
    rows = {r[0] for r in st.con.execute("SELECT DISTINCT pose_id FROM current_state")}
    assert rows == {rec["pose_id"]}
    ev = [r[0] for r in st.con.execute("SELECT pose_id FROM events WHERE pose_id IS NOT NULL")]
    for p in ev:
        assert p.startswith("BP1-") and len(p) == 20 and all(ch in "0123456789abcdef" for ch in p[4:])

def test_rehome_without_faults_restores_library_id(env):
    st, r = env["st"], env["recs"]
    st.update_from_sim(r[0]); st.mark_unknown(None, reason="power_cut"); st.rehome(r[0]["pose_id"])
    assert st.current_record()["pose_id"] == r[0]["pose_id"]

def test_bad_record_id_rejected(env):
    r = dict(env["recs"][0], pose_id="BP1-0000000000000000")
    with pytest.raises(ValueError):
        env["st"].update_from_sim(r)

def test_no_generator_modules_loaded():
    assert not [m for m in sys.modules if "bod_regen" in m or "bod_nextpass" in m]
