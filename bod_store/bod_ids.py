"""Pure, stdlib-only canonical checksum / pose_id derivation per BOD_EMBODIMENT_SPEC.md §2.8.
Implemented independently from the written rule; does NOT import tools/bod_regen.py,
tools/bod_nextpass_edit.py or tools/bod_validate.py."""
import copy, hashlib, json, math

FAULT_CODE = {"ok": "0", "weak": "1", "stuck": "2", "stall": "2", "disconnected": "3", "dead": "3",
              "fault": "3", "unknown": "4", "offline": "5", "absent": "6"}  # spec §2.5

def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def quantize(x, bin_deg=5, lo=None, hi=None):
    """§2.4: q = clamp(floor(x/bin + 0.5)*bin, minIn, maxIn); None stays None."""
    if x is None:
        return None
    q = int(math.floor(x / bin_deg + 0.5) * bin_deg)
    if lo is not None: q = max(q, lo)
    if hi is not None: q = min(q, hi)
    return q

def canonical_payload(rec):
    return canon({"cal": rec["calibration_id"], "fm": rec["fault_mask"],
                  "j": {j["name"]: j["q_deg"] for j in rec["joint_vector"]},
                  "p": rec["body_profile"], "q": "grid%d" % rec["quantization"]["bin_deg"],
                  "v": rec["schema_version"]})

def joint_checksum(rec):
    return sha256_hex(canonical_payload(rec))

def pose_id(rec):
    return "BP1-" + joint_checksum(rec)[:16]

def svo_index(profile, svo):
    return 0 if profile == "finger_starter_v1" else int(svo[3:]) - 1

def remint(rec):
    """Return a copy of rec with canonical_payload, joint_checksum, pose_id, record_checksum recomputed."""
    r = copy.deepcopy(rec)
    cp = canonical_payload(r)
    jc = sha256_hex(cp)
    r["pose_id"] = "BP1-" + jc[:16]
    c = r.setdefault("checksums", {})
    c["canonical_payload"], c["joint_checksum"] = cp, jc
    c.pop("record_checksum", None)
    c["record_checksum"] = sha256_hex(canon(r))
    return r
