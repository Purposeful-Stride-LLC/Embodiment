#!/usr/bin/env python3
"""Independent validator for bod_pose_state.examples.json (v0.3, 2026-10-13).

INDEPENDENCE: this file does NOT import, exec or read tools/bod_regen.py or
tools/bod_nextpass_edit.py (the generators). Canonicalisation and hashing are
re-implemented here from the written rule in BOD_EMBODIMENT_SPEC.md section 2.8,
using only the Python standard library + jsonschema. It also asserts at exit that
no generator module was loaded.

Checks per record:
  A. JSON Schema draft 2020-12 (+ FormatChecker) against bod_pose_state.schema.json
  B. canonical payload rebuilt from the record's own fields == stored payload
  C. joint_checksum == SHA-256(payload); pose_id == 'BP1-' + joint_checksum[:16]
  D. record_checksum == SHA-256(canonical record minus record_checksum)
  E. policy (spec v0.3): classic31_v1 head.rollNeck + torso.midStom are 'absent'
     with q_deg null and fault-mask digits 6 (OPEN-W9); no gravity joint is
     status 'ok' while de-energised (autoDisable -> unknown, spec 5.1); every
     'absent' joint has q_deg null and its servos' fault digits = 6.
Plus: pose_ids unique across the file; built-in negative controls (--negatives).
Read-only. Exit 0 only if every record passes (and every negative is rejected).
Usage: python tools/bod_validate.py [--negatives]
"""
import copy, hashlib, json, os, sys
from jsonschema import Draft202012Validator, FormatChecker

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAVITY = {"bicep", "shoulder", "omoplate", "neck", "topStom", "midStom"}
W9_ABSENT = {"head.rollNeck", "torso.midStom"}

def _canon(obj):  # spec 2.8: sorted keys, ',' ':' separators, ensure_ascii
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

def _h(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _payload(rec):
    return _canon({"cal": rec["calibration_id"], "fm": rec["fault_mask"],
                   "j": {jt["name"]: jt["q_deg"] for jt in rec["joint_vector"]},
                   "p": rec["body_profile"], "q": "grid%d" % rec["quantization"]["bin_deg"],
                   "v": rec["schema_version"]})

def _svo_digit(rec, svo):
    if rec["body_profile"] == "finger_starter_v1":
        return rec["fault_mask"][0]
    return rec["fault_mask"][int(svo[3:]) - 1]

def check(rec, val):
    errs = [f"schema {list(e.path)}: {e.message}" for e in val.iter_errors(rec)]
    if errs:
        return errs  # structural failure; field-level checks would be unreliable
    c = rec["checksums"]
    cp = _payload(rec)
    jc = _h(cp)
    tmp = copy.deepcopy(rec); tmp["checksums"].pop("record_checksum", None)
    if c["canonical_payload"] != cp: errs.append("canonical_payload != rebuilt payload")
    if c["joint_checksum"] != jc: errs.append("joint_checksum != sha256(rebuilt payload)")
    if rec["pose_id"] != "BP1-" + jc[:16]: errs.append("pose_id != BP1- + sha256(payload)[:16]")
    if c.get("record_checksum") != _h(_canon(tmp)): errs.append("record_checksum mismatch")
    for jt in rec["joint_vector"]:
        ch = jt["name"].split(".", 1)[1]
        if rec["body_profile"] == "classic31_v1" and jt["name"] in W9_ABSENT and jt["status"] != "absent":
            errs.append(f"policy: {jt['name']} must be absent until OPEN-W9 settled")
        if jt["status"] == "absent":
            if jt["q_deg"] is not None: errs.append(f"policy: absent {jt['name']} has q_deg")
            for s in jt.get("svo", []):
                if _svo_digit(rec, s) != "6": errs.append(f"policy: {s} fault digit != 6 for absent channel")
        if ch in GRAVITY and jt["status"] == "ok" and jt.get("energized") is False:
            errs.append(f"policy: gravity joint {jt['name']} de-energised but status ok (spec 5.1: unknown until re-homed)")
    return errs

def main():
    schema = json.load(open(os.path.join(ROOT, "bod_pose_state.schema.json"), encoding="utf-8"))
    ex = json.load(open(os.path.join(ROOT, "bod_pose_state.examples.json"), encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    print("schema meta-check (draft 2020-12): PASS")
    val = Draft202012Validator(schema, format_checker=FormatChecker())
    fails = 0
    for i, e in enumerate(ex, 1):
        errs = check(e, val)
        fails += bool(errs)
        print(f"[{i}] {e.get('pose_id')} {e.get('pose_name')} ({e.get('body_profile')}): "
              f"schema+checksums+policy -> {'FAIL' if errs else 'PASS'}")
        for m in errs: print("    ", m)
    ids = [e.get("pose_id") for e in ex]
    if len(set(ids)) != len(ids):
        fails += 1; print("FAIL: duplicate pose_id in file")
    else:
        print(f"pose_id uniqueness: PASS ({len(ids)} distinct)")
    neg_fail = 0
    if "--negatives" in sys.argv:
        base = ex[0]
        def mut(f):
            r = copy.deepcopy(base); f(r); return r
        negs = {
            "WP1- pose_id": mut(lambda r: r.__setitem__("pose_id", "WP1-8d7dbc9eeb7a96f9")),
            "whi.pose.v1 version": mut(lambda r: r.__setitem__("schema_version", "whi.pose.v1")),
            "tampered q_deg (shoulder 30->35)": mut(lambda r: [j.__setitem__("q_deg", 35) for j in r["joint_vector"] if j["name"] == "rightArm.shoulder"]),
            "tampered record field (confidence)": mut(lambda r: r["provenance"].__setitem__("confidence", 0.9)),
            "rollNeck present (status ok, q 90)": mut(lambda r: [j.update(status="ok", q_deg=90) for j in r["joint_vector"] if j["name"] == "head.rollNeck"]),
            "gravity joint de-energised + ok": mut(lambda r: [j.__setitem__("energized", False) for j in r["joint_vector"] if j["name"] == "rightArm.shoulder"]),
        }
        for k, r in negs.items():
            rejected = bool(check(r, val))
            neg_fail += not rejected
            print(f"negative control '{k}': {'REJECTED (ok)' if rejected else 'ACCEPTED (BAD)'}")
    gen = [m for m in sys.modules if "bod_regen" in m or "bod_nextpass" in m]
    print("generator modules loaded:", gen or "none")
    if gen: fails += 1
    print(f"RESULT: {len(ex) - min(fails, len(ex))}/{len(ex)} pass, {fails} fail"
          + (f"; negatives {len(negs) - neg_fail}/{len(negs)} rejected" if "--negatives" in sys.argv else ""))
    return 1 if (fails or neg_fail) else 0

if __name__ == "__main__":
    sys.exit(main())
