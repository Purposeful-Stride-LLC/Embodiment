# BOD — Body / Embodiment-Information System for an InMoov-class Humanoid

> **Status: proposed design.** Every concept in this document (pose IDs, fact keys, ID prefixes, triggers, store layout) is a proposal. It has been exercised in simulation only and is not validated on hardware.

> **Renamed 2026-10-13:** this system was formerly called "WHI" (and briefly proposed as "EMB" in the v0.1 design review (not included)). **BOD** naming approved by the maintainers 2026-10-13. In NOVA, "WHI" keeps its only meaning: the NOVA addressing scheme (proposed). Old path `WHI_EMBODIMENT_SPEC.md` is now a redirect stub to this file.

| Field | Value |
|---|---|
| Owner | Purposeful Stride LLC |
| Scope | Lunar-base plan, crew-training robot section. Internal, non-commercial R&D |
| Status | **Living specification, v0.3 (draft), 2026-10-13** (v0.3 = design-review next-pass fixes from the v0.2 design review (not included); v0.2 = BOD naming regen). Nothing here has been built or run on hardware yet |
| Hardware source of truth | `INMOOV_DESIGN_DOSSIER.md` v0.2 (cited as **[D §x]**) and the upstream repos `MyRobotLab/inmoov` @ `c450e19` and `MyRobotLab/inmoov2` @ `5715ec6b` (cited as **[REPO:inmoov]** / **[REPO:inmoov2]**) |
| Companion files | `bod_pose_state.schema.json` (PoseState JSON Schema, draft 2020-12, `$id` `urn:purposeful-stride:bod:pose-state:v1`). `bod_pose_state.examples.json` (the 8 example records from §7; validated 8/8 by the independent validator `tools/bod_validate.py`, see `BOD_SCHEMA_PROOF.md`). Generators: `tools/bod_regen.py` (deterministic checksum/pose_id regenerator) and `tools/bod_nextpass_edit.py` (v0.3 content edit). `BOD_NEXTPASS_NOTES.md` (v0.3 change summary) |
| License note | InMoov designs © Gaël Langevin, CC BY-NC 3.0 [D §5.4]. This spec describes software and data, not the designs |

**Changelog**

| Date | Version | Change |
|---|---|---|
| 2026-10-13 | v0.3 | **design-review next-pass fixes** (the v0.2 design review (not included)). (1) §5.1/§7.1/§2.5: a gravity joint de-energised by autoDisable is now status `unknown`, `q_deg = null` until re-homed (was: lower confidence); policy = autoDisable off on gravity joints during plans. (2) `head.rollNeck` and `torso.midStom` are `absent` in all six `classic31_v1` examples until OPEN-W9 is settled; all six classic31 pose_ids/checksums re-minted (§2.8 rule unchanged); E6 re-targeted from midStom to the topStom pair (`rest+topStom_B_dead`) because a midStom fault cannot exist on an absent channel; E1/E5/E6 gravity joints now held energised. Finger Starter and right-arm examples unchanged. (3) OPEN-W23 names design review's proposal `BT1-` / `BPL1-`, shown in §4.1/§8.1/§8.2 as *pending maintainer approval*; §8.2 chain NOT rebuilt. (4) §6.2 separate-store requirements S1–S3 (runs with NOVA absent; not on removable drive; retention policy). (5) `parts_acquisition.csv` rows B-44 (INA219) / B-45 (INA226). (6) `tools/bod_validate.py` rewritten to be independent of the generators. **No NOVA.db write.** Validator/executor still not started. |
| 2026-10-13 | v0.2 | **BOD naming approved by the maintainers 2026-10-13.** Renamed WHI/EMB → BOD throughout: schema/version `bod.pose.v1` (also `bod.edge.v1`, `bod.plan.v1`, `bod.seq.v1`); pose ID prefix `BP1-` (was `WP1-`; `EP1-` was proposed, never minted); NOVA keys `Ax-BOD-POSE`, `Ax-BOD-SUBPOSE`, `Ax-BOD-EDGE`, `Ax-BOD-FAULT`, `Ax-BOD-CAL` (libraries) and `Tx-BOD-PLAN`, `Tx-BOD-MISMATCH` (staging) — replacing `Tx-WHI-*` / `Ax-EMB-*` / `Tx-EMB-*`. All 8 example pose_ids, joint_checksums and record_checksums regenerated (§2.8 rule unchanged); worked examples in §2.8, §7, §8.2 updated. Files renamed to `BOD_EMBODIMENT_SPEC.md`, `bod_pose_state.schema.json`, `bod_pose_state.examples.json`, with redirect stubs at the old paths. **NOVA.db writes still held pending validator/executor PROOF** — no NOVA.db write has been made or planned by this change. `force_class` remains a PLACEHOLDER. The 5 Hz observation stream stays out of NOVA (local store only). design review's other review points remain OPEN (§10.1). Open-question IDs keep their historical `OPEN-Wn` labels so existing references do not break. |
| 2026-10-13 | v0.1 | Initial draft (as "WHI"). Reviewed in the v0.1 design review (not included). |

**Conventions**
- **⚠ OPEN-Wn** marks an open BOD question (IDs keep the historical `W` letter). All of them are collected in §10.
- **PLACEHOLDER** marks a number chosen by this spec to make the design concrete. It is not measured and has no source. Each one must be replaced by a measurement or a sign-off.
- Angles are **MRL input-domain degrees**: the numbers that gestures pass to `moveHand`/`moveArm`/`moveTo`, *before* `map()` [D §1.4]. Physical servo angles come out of `map()` and the calibration in force.

---

## 1. Intent

BOD (embodiment information) is everything the robot knows about **its own body**. It has three parts:

1. **Where the body is:** the current pose (every joint), plus where the robot stands (location).
2. **What condition the body is in:** health per servo and per limb, power and E-stop state, and balance or tip risk.
3. **How the body may move:** a graph of safe transitions between poses.

The on-board LLM (Ollama through MRL's `LLM` service [D §3.1]) never drives servos from its own memory. It works only through BOD:

- **Read.** It gets the current `PoseState` from BOD. That state is a record with a checksum, not a guess the model makes.
- **Plan.** It retrieves candidate goal poses and transitions from the BOD RAG index, then proposes a `MotionPlan`: an ordered, checksummed sequence of safe transitions from the current pose to the goal.
- **Gate.** A deterministic validator recomputes every checksum and checks balance and fault preconditions. Only then does anything execute.
- **Verify.** After each step, BOD compares the expected pose with the estimated or sensed pose. A mismatch sends the robot into a fail-safe and writes a log entry.

**Design rules**
- **R1.** The LLM proposes. The validator disposes. The LLM never computes or asserts a checksum. It only quotes IDs it retrieved.
- **R2.** Every stored state is labelled honestly as *commanded*, *estimated* or *sensed*. On a stock InMoov almost everything is *estimated* (§5).
- **R3.** We do not store "every possible continuous position" (§3). We store a small library and generate neighbours on demand.
- **R4.** A fault is part of the state. A pose with a dead servo is a different `pose_id` from the same pose on a healthy body.
- **R5.** The physical E-stop (SW1 [D §4.5, §8.2]) always overrides BOD. BOD records the E-stop state. It never assumes software can make motion safe.

---

## 2. State model

### 2.1 Body profiles

The joint vector and fault mask both depend on how much of the robot exists. That matches the build order in §9.

| `body_profile` | MRL channels in joint vector | Physical servos in fault mask | Source |
|---|---|---|---|
| `finger_starter_v1` | 1 (`rightHand.index`, as named in the legacy test script) | 1 (SVO00) | [D §2.1, §1.4, §8.2] |
| `right_arm_v1` | 10 (right hand 6 + right arm 4) | 10 (SVO01–SVO10) | [D §8 scope, §8.2] |
| `classic31_v1` | **28** | **31** | [D §2.2: "31 physical servos / 28 MRL channels"] |

Why there are two counts: the **rollNeck** pair (master + slave) shares one channel, and so does each **stomach** pair (topStom A/B, midStom A/B) [D §2.2]. A joint vector over 28 channels cannot express "one of the two midStom servos is dead". The fault mask is kept **per physical servo** (31 digits) for exactly that reason.

i2Head (+~17 servos, OPEN-Q5), the i2Hand option, eyelids and the extra face servos in `InMoov2FullStart.py` (46 servo services in total [D §2.2]) are **out of scope for v1**. They would get their own profile, e.g. `i2_v1` (⚠ OPEN-W11).

### 2.2 Canonical channel list (`classic31_v1`)

These are channel names as MRL services (prefix `i01.` dropped).
- **Servo type** and **SVO refdes** come from [D §2.2, §8.2].
- **Input limits** and **rest** come from the `map(minIn,maxIn,…)` and `setRest()` lines in `inmoov2/system/startScripts/InMoov2FullStart.py` [REPO:inmoov2]. These match the legacy `skeleton_*.config.default` except where noted.
- **Pins** are the default pins from the same start script and the legacy config [D §1.6, §8.5]. They are listed for traceability only. BOD does not use pins.

| # | Channel | SVO | Servo (BOM default) | Input limits (deg) | Output map (Nixie script) | Rest | Default pin |
|---|---|---|---|---|---|---|---|
| 1–5 | `rightHand.{thumb,index,majeure,ringFinger,pinky}` | SVO01–05 | HK15298B | 0–180 | 0–180 | 2 (legacy 0) | 2,3,4,5,6 on `right` |
| 6 | `rightHand.wrist` | SVO06 | MG996R | 0–180 | 0–180 | 90 | 7 |
| 7 | `rightArm.bicep` | SVO07 | HS-805BB (pot hacked) | 0–90 | 0–90 | 0 | 8 |
| 8 | `rightArm.rotate` | SVO08 | HS-805BB | 40–180 | 40–180 | 90 | 9 |
| 9 | `rightArm.shoulder` | SVO09 | HS-805BB | 0–180 | 0–180 | 30 | 10 |
| 10 | `rightArm.omoplate` | SVO10 | HS-805BB | 10–80 | 10–80 | 10 | 11 |
| 11–16 | `leftHand.*` (same order) | SVO11–16 | as right | 0–180 | 0–180 (wrist inverted) | 2 / wrist 90 | 2–7 on `left` |
| 17–20 | `leftArm.{bicep,rotate,shoulder,omoplate}` | SVO17–20 | HS-805BB | as right | as right | as right | 8–11 on `left` |
| 21 | `head.neck` | SVO21 | HS-805BB | 0–180 | 20–160 | 90 | 12 |
| 22 | `head.rothead` | SVO22 | HS-805BB | 0–180 | 30–150 | 90 | 13 |
| 23 | `head.rollNeck` | SVO23 + SVO24 | 2× HK15298B, 1 channel | 0–180 | **60–130** (legacy 20–170) ⚠ | 90 | **12 on `right`** (legacy 30) ⚠ OPEN-Q19 |
| 24 | `head.jaw` | SVO25 | HK15298B | 0–180 | 10–25 | 0 | 26 |
| 25 | `head.eyeX` | SVO26 | DS929HV class | 0–180 | **60–120** (legacy 60–100) ⚠ | 90 | 22 |
| 26 | `head.eyeY` | SVO27 | DS929HV class | 0–180 | **60–120** (legacy 50–100) ⚠ | 90 | 24 |
| 27 | `torso.topStom` | SVO28 + SVO29 | 2× HS-805BB, continuous-rotation hack, shared pot | 60–120 | 60–120 | 90 | 27 |
| 28 | `torso.midStom` | SVO30 + SVO31 | 2× HS-805BB, shared board | **0–180** (legacy input 60–120) ⚠ | 0–180 | 90 | 28 |

Conflicts flagged in the table, not resolved:
- **rollNeck map and pin** differ between the Nixie script and the legacy config (OPEN-Q19).
- **eyeX/eyeY output ranges** differ.
- **midStom input range** differs.
- **v0.3 interim rule (design review §3.3 / §4.2):** until OPEN-W9 is settled in one signed calibration register, `head.rollNeck` (SVO23+SVO24) and `torso.midStom` (SVO30+SVO31) are carried as status **`absent`** (`q_deg = null`, fault digits `6`) in `classic31_v1` records. They are never commanded and every edge touching them is pruned (§4.2 rule 1). Their rows above stay for traceability only.
- The Nixie script also defines a **`torso.lowStom`** servo on pin 29, and `moveTorso` takes 3 arguments in the gestures. The dossier says the low stomach has **no servo** (pole mount) [D §2.2, §5.2]. v1 excludes `lowStom` from `classic31_v1` (⚠ OPEN-W10).

`calibration_id` (§2.8) pins which variant is in force. The table must be regenerated from the final YAML config set (OPEN-Q2, OPEN-Q19).

Gesture argument order: the gestures pass `moveArm(side, a, b, c, d)` and `moveHand(side, a…f)`. BOD assumes **bicep, rotate, shoulder, omoplate** and **thumb, index, majeure, ringFinger, pinky, wrist**. That order is consistent with the configured limits: for example, omoplate values in `ready.py`/`relax.py` stay inside 10–80. inmoov.fr documents the order [D §1.4], but it has not been checked against the InMoov2 Java source (⚠ OPEN-W13). `moveHead` takes 2 to 6 arguments in different gestures, and its order beyond neck/rothead is unverified.

### 2.3 `PoseState` record (summary; full definition in `bod_pose_state.schema.json`)

| Field | Content |
|---|---|
| `schema_version` | `bod.pose.v1` |
| `body_profile` | see §2.1 |
| `calibration_id` | ID of the calibration register / MRL config set in force (OPEN-Q2). **Part of the hash.** |
| `pose_id` | `BP1-` + first 16 hex chars of `joint_checksum` |
| `pose_name` | optional library name: `rest`, `ready`, `ready+RA_offline`, … |
| `joint_vector[]` | per channel: `name`, `svo[]`, `q_deg` (quantized, or null), `commanded_deg`, `estimated_deg`, `sensed_deg`, `provenance`, `confidence`, `status`, `energized` |
| `quantization` | `{scheme, bin_deg, rounding}` (§2.4) |
| `fault_mask` | one digit per physical servo (§2.5) |
| `health` | per-limb roll-up, active `modifiers[]` (§7), `estop`, `power_rails` (REG0–REG6 [D §8.2]) |
| `balance_profile` | `com_class`, `tip_risk`, `support_note`, `motion_allowed`, `reasons[]`, `model` (§2.6) |
| `location` | `frame` (base/world/unknown), `known`, `site_id`, `xyz_m`, `yaw_deg`, `source` (§2.7) |
| `provenance` | overall (weakest live joint), `confidence` 0–1, `encoder` (TimeEncoder by default), `source_system` (hardware/sim/replay) |
| `checksums` | `algo`, `canonical_payload`, `joint_checksum`, `record_checksum`, `sequence_checksum`, `plan_id`, `step_index` (§2.8, §8) |
| `contact` | optional fingertip pressure level 0–3 per sensor [D §1.7] |
| `observed_at` | timestamp (UTC in storage, displayed in CT) |

### 2.4 Quantization

**Why continuous enumeration is impossible.**
- The joints are continuous. MRL also accepts fractional degrees: the gestures use values like `90.20`.
- Even at the 1° resolution of the input domain, `classic31_v1` has ∏(range+1) ≈ **10^61** joint combinations (the computation is in §3.1). No index of any size holds that.

**v1 scheme: `grid+detent`.**
1. **Identity grid.** Each angle is snapped to a **5° grid** anchored at 0: `q = clamp(floor(x/5 + 0.5)·5, minIn, maxIn)`.
   - All configured limits (0, 10, 40, 60, 80, 90, 120, 180) are multiples of 5, so clamping stays on-grid.
   - Two states in the same 5° cell share a `pose_id`.
   - 5° is a **PLACEHOLDER** (⚠ OPEN-W1). It is coarser than the jaw's whole useful mechanical range: the jaw output map is 10–25° for input 0–180, so 5 input degrees ≈ 0.4 output degrees. It is probably finer than a TimeEncoder estimate can honestly claim for loaded arm joints.
2. **Named detents** (library level). Each joint also has 2–5 named detents, for example:
   - fingers: `open` 0, `half` 90, `closed` 180
   - wrist: 0 / 90 / 180
   - shoulder: `down` 30 (rest) / `mid` 75 / `high` 170
   - omoplate: 10 / 45 / 80
   - neck: 45 / 90 / 135
   - jaw: 0 / 90
   - topStom: 75 / 90 / 105
   
   Every detent is *also* a grid point, so detents and the grid hash identically. Detent values are PLACEHOLDERS, apart from the rest values, which come from the config, and gesture-derived values such as `ready`.
3. **Unknown is a value.** `q_deg = null` whenever the joint position cannot be trusted: status disconnected/offline/absent/unknown, or a gear slip is suspected. `null` is hashed as JSON `null`. A pose that has an unknown joint never collides with a known pose.

Per-joint bin size is a possible v2 refinement: fingers at 10°, jaw at 2 detents, eyes at 10° (OPEN-W1). The schema carries `bin_deg` per record so the scheme can change without ambiguity.

### 2.5 Fault mask and health

`fault_mask` is a digit string, one digit per physical servo in SVO refdes order: `classic31_v1` = SVO01…SVO31, `right_arm_v1` = SVO01…SVO10, `finger_starter_v1` = SVO00.

| Code | Status | Meaning | Effect |
|---|---|---|---|
| 0 | ok | responds; no anomaly observed | normal |
| 1 | weak | moves, but slow, short or slipping: gear slip, half of a pair dead, low rail voltage | velocity cap, reduced edges |
| 2 | stuck | does not move when commanded (mechanical bind or stalled) | channel frozen at `stuck_at_deg`; servo disabled to avoid stall heating |
| 3 | disconnected | no signal path / no response / dead servo | channel excluded; position unknown |
| 4 | unknown | status not established (boot, after E-stop, after a link drop, **after autoDisable de-energises a gravity joint** §5.1) | no motion on that channel until homed |
| 5 | offline | intentionally disabled or de-powered (rail off, `disable()`) | channel excluded; may hang under gravity |
| 6 | absent | not built yet, or excluded pending calibration (v0.3: rollNeck/midStom, OPEN-W9) | excluded; not an error (MVP phases) |

The 7th code `absent` is an addition to the six requested statuses. Without it, a Finger Starter or one-arm build would look like a robot with 30 or 21 failures.

`health.limbs` rolls up the worst code per subassembly: rightHand, leftHand, rightArm, leftArm, head, torso, or fingerStarter. `absent` is not a fault and does not degrade the roll-up (a head whose only non-`ok` channel is the absent rollNeck rolls up as `ok`). **Bit-filter form for RAG metadata:** `limb_ok_bits` is a 6-bit integer, one bit per limb with every servo `ok` or `absent`, plus `fault_digest` = first 8 hex chars of SHA-256(fault_mask). Both are used in §6.

### 2.6 Balance profile

**What the robot stands on.** The classic InMoov is an upper body on a **pole mount**: the low stomach has no servo [D §2.2, §5.2]. There are no legs. So:
- "Balance" here means **tip-over of the whole robot on its stand**, plus **load on the stand and pole**. It does not mean gait stability.
- "Sit", "stand" and "kneel" are not physical actions for the classic robot (§3.3).
- The stand/base footprint, robot mass and per-part masses are **not in the dossier**. The JME sim does not model torque or mass [D §1.3]. A real CoM computation is therefore impossible today.

**v0 estimator (`model: rules_v0_placeholder`).** This is a rule table. Every threshold is a PLACEHOLDER (⚠ OPEN-W5, OPEN-W6).

| Input | Rule (PLACEHOLDER thresholds, input degrees) |
|---|---|
| Arm reach per side | `tucked` if shoulder ≤ 40; `mid` if 40–90; `high` if > 90. Omoplate > 45 = `lateral` |
| Torso lean | \|topStom − 90\| ≥ 20 → lean toward that side |
| Payload | any fingertip contact level ≥ 2 on a closing hand → `load` on that side |
| Unknown gravity joint | any of bicep/shoulder/omoplate/neck/topStom/midStom with `q_deg = null` while powered → CoM `unknown`. This includes a gravity joint made `unknown` by autoDisable (§5.1). `absent` channels are excluded (not built / not commanded) |

| Result | Condition |
|---|---|
| `tip_risk = low` | no high reach, no lean, no load |
| `tip_risk = med` | exactly one of: high/lateral reach, lean, load; or CoM `unknown` |
| `tip_risk = high` | lean and reach toward the same side; or load with high reach; or two or more `med` triggers |
| `motion_allowed = false` | tip_risk high; or E-stop not `released`; or a `stuck`/`weak` fault on a gravity-loaded joint in the current motion set; or estimator `unknown` with the stand not certified as clamped |

`com_class` takes one of `centered`, `forward`, `forward_far`, `lateral_left`, `lateral_right`, `rear`, `unknown`.

**Lunar note.** Static tipping is geometric: the CoM projection must stay inside the support polygon. That test holds at any g. At lunar gravity (~1.62 m/s²) the restoring moment is about 1/6 of Earth's, so a fast arm swing that is harmless on Earth can matter more. Do not carry Earth-tuned velocity limits to a lunar habitat without re-analysis (⚠ OPEN-W5).

### 2.7 Location

The classic robot does not locomote, so its location is the location of its mount.

| Field | Meaning |
|---|---|
| `frame` | `base` (robot mount frame, the default) / `world` (habitat frame) / `unknown` |
| `known` | true only if a source vouches for it |
| `site_id` | operator-entered bay, room or training-cell ID |
| `xyz_m`, `yaw_deg` | only when a fixture registry or fiducial gives them |
| `source` | `operator_entry`, `fixture_registry`, `fiducial` or `unknown` |

The v1 default is `{frame: base, known: false}`. Location is **not** in the `pose_id` hash: the same pose in two rooms is the same body pose. It goes into the observation log and the `record_checksum` instead.

### 2.8 Checksums

**Canonical payload.** This is the only thing that defines `pose_id`. It is a JSON object with exactly these keys:

```text
{"cal": <calibration_id>, "fm": <fault_mask>, "j": {<channel>: <q_deg int|null>, ...},
 "p": <body_profile>, "q": "grid5", "v": "bod.pose.v1"}
```

It is serialised with **sorted keys**, separators `,` and `:` with no whitespace, `ensure_ascii`, and integers only. In Python that is `json.dumps(o, sort_keys=True, separators=(',',':'), ensure_ascii=True)`.

- `joint_checksum` = SHA-256 hex of that UTF-8 string.
- `pose_id` = `"BP1-" + joint_checksum[:16]` (literal prefix `BP1-` followed by the first 16 lowercase hex characters of the SHA-256 `joint_checksum`). That is 64 bits. With ≤ 10^5 stored states, the birthday collision probability is ~ n²/2^65 ≈ 3×10⁻¹⁰. The validator always compares the **full** 64-hex checksum.
- `record_checksum` = SHA-256 of the canonical serialisation of the whole record, with `record_checksum` itself removed. It covers confidence, location, contact and timestamps, and is used for storage integrity.
- `sequence_checksum` is a hash chain over a plan (§8).

**Worked example (real, computed with the rule above; re-minted v0.3).** This is the canonical payload of `rest` on `classic31_v1` with calibration `cal-classic31-nixie-fullstart-defaults-v0`, with `head.rollNeck` and `torso.midStom` `absent` (§2.2 interim rule):

```text
{"cal":"cal-classic31-nixie-fullstart-defaults-v0","fm":"0000000000000000000000660000066","j":{"head.eyeX":90,"head.eyeY":90,"head.jaw":0,"head.neck":90,"head.rollNeck":null,"head.rothead":90,"leftArm.bicep":0,"leftArm.omoplate":10,"leftArm.rotate":90,"leftArm.shoulder":30,"leftHand.index":0,"leftHand.majeure":0,"leftHand.pinky":0,"leftHand.ringFinger":0,"leftHand.thumb":0,"leftHand.wrist":90,"rightArm.bicep":0,"rightArm.omoplate":10,"rightArm.rotate":90,"rightArm.shoulder":30,"rightHand.index":0,"rightHand.majeure":0,"rightHand.pinky":0,"rightHand.ringFinger":0,"rightHand.thumb":0,"rightHand.wrist":90,"torso.midStom":null,"torso.topStom":90},"p":"classic31_v1","q":"grid5","v":"bod.pose.v1"}
→ joint_checksum ddcab2230df85dcc873fd89eb83dfa88160791ababa9d0a51cd9992e85a8ac4a
→ pose_id        BP1-ddcab2230df85dcc
```

(v0.2 value with rollNeck/midStom present, now retired: `BP1-98359d587cf7b928`.)

The finger rest value is 2, which quantizes to 0.

**What the checksums do not do.** Plain SHA-256 detects corruption, reordering and stale or mismatched IDs. It does **not** stop deliberate forgery, because anyone can recompute it. If tamper-resistance matters, for example LLM output passing through untrusted channels, switch to HMAC-SHA-256 with a key held only by the validator (⚠ OPEN-W19).

---

## 3. Combinatorial strategy

### 3.1 The math

The table below takes per-joint input ranges from §2.2 and computes ∏(range/bin + 1) over the 28 channels of `classic31_v1`:

| Scheme | Joint combinations |
|---|---|
| 1° grid | ≈ **10^61.1** |
| 5° grid (v1 identity grid) | ≈ **10^41.9** |
| 10° grid | ≈ **10^33.9** |
| 3 detents per joint | 3^28 ≈ **2.3 × 10^13** |
| × fault mask, 7 codes on 31 servos | 7^31 ≈ **10^26.2** more |

- Even the crude **3-detent** space is too large to check: at 1 µs per balance check it would take ~266 days, before any fault variants are added.
- With faults on a 5° grid, the space is ~10^68. "Store every position" is not a design option.
- Most of these states are also mechanically meaningless or unsafe: self-collision, tip, stall against mechanical limits.

### 3.2 Compositional sub-poses

Full poses are assembled from sub-pose libraries, one per subassembly:

| Sub-pose family | Channels | v1 library size (PLACEHOLDER) |
|---|---|---|
| Hand shape × wrist | 5 fingers + wrist | 12 shapes (open, closed, rest, half, point, pinch, thumbs_up, count_1…5) × 3 wrist detents = **36 per hand** |
| Arm | bicep, rotate, shoulder, omoplate | **15 per side** (rest, tucked, ready, reach_fwd_low/mid/high, reach_lat, present, armsUp, …) |
| Neck / gaze | neck, rothead (rollNeck `absent` in v1 pending OPEN-W9) | **9** (3×3) |
| Face | jaw, eyeX, eyeY | **10** (jaw 2 × eyes 5) |
| Torso | topStom, midStom | **9** (3×3) |

The free product is 36² × 15² × 9 × 10 × 9 ≈ **2.4 × 10^8** full poses. That is still too many to precompute with balance and collision checks, so it is **not stored**. *v0.3 note:* while `torso.midStom` is `absent` (OPEN-W9) the torso family is topStom-only (3 detents), so the interim free product is ≈ **7.9 × 10^7** and the sub-pose set is **124** documents; the 9-entry torso / 130-document figures are the target once calibration is settled.

Only these are stored:
- the **130 sub-pose documents** (36×2 + 15×2 + 9 + 10 + 9)
- the curated full-body poses below

### 3.3 Named canonical full-body poses

The library is seeded from real `inmoov2/gestures/*.py` values where they exist. There are 223 top-level gesture files [D §1.2]. Many are multi-step sequences, so each step is a candidate pose.

| Name | Source | Notes |
|---|---|---|
| `rest` | `gestures/rest.py` → each servo's `setRest()` | Home / boot target |
| `ready` ("stand-ready") | `gestures/ready.py` | Stands in for "stand-ready". The classic robot has no legs, so "stand" means upright, attentive posture |
| `relax` | `gestures/relax.py` | Includes torso 95/90 |
| `point_center_right` | `gestures/showObject.py` `showToCenter()` | Right index 0, other fingers 180 = point |
| `show_left`, `show_right` | same file | |
| `look_at_this` | `gestures/lookAtThis.py` | Two-arm forward reach |
| `arms_up_*` | `gestures/armsUp.py` (each step) | High reach, so `tip_risk ≥ med` until a mass model exists |
| `hand_open` / `hand_closed` | `gestures/handopen.py` / `handclose.py` | Hand sub-pose only |
| `failsafe_*` | §4.3 | Sinks |
| `sit`, `kneel`, `brace` | — | **Not applicable to `classic31_v1`** (no legs). Reserved names, so a future legged or base variant can define them |

Target is **40–60** canonical full-body poses for v1.

### 3.4 Fault-augmented variants

A fault profile is a pattern over the fault mask: a limb offline, a single HS-805BB stuck, half of a pair dead, and so on. Applying it to a canonical pose does three things:
1. It sets the affected channels to `null` or `stuck_at`.
2. It recomputes balance.
3. It produces a **new `pose_id`**.

v1 target is **~25–40 fault profiles**:
- 6 limb-offline cases
- 14 single-HS-805BB stuck/dead cases (the gravity-loaded servos)
- 4 pair-half-dead cases (rollNeck, topStom and midStom pairs). *v0.3:* only the 2 topStom cases can be minted while rollNeck and midStom are `absent` (OPEN-W9); example E6 is one of them
- rail-off cases (REG1–REG6)

Fault variants needed ≈ 60 × 40 ≈ **≤ 2,400**. Many collapse to the same ID: any pose with the right arm offline loses its right-arm detail.

### 3.5 On-demand neighbours

The planner can call `neighbors(pose_id)`. It returns:
- ±1 grid step on any single movable channel (≤ 56 states), and
- swaps of one sub-pose to an adjacent library entry.

Each neighbour is validated (limits, fault preconditions, balance, collision class), then cached in an **LRU of 20,000 states** (PLACEHOLDER). Neighbours are never bulk-precomputed.

### 3.6 Rough cardinality of what is actually indexed

| Item | Count (estimate) |
|---|---|
| Sub-pose docs | ~130 |
| Canonical full-body poses | 40–60 |
| Fault-augmented variants | ≤ 2,400 |
| Transition edges, canonical | ~60 nodes × ~6 = ~360 |
| Transition edges, fault variants | ~2,400 × ~4 ≈ ~10,000 |
| On-demand neighbour cache | ≤ 20,000 (LRU) |
| **Total indexed documents** | **~10^4 – 3×10^4** |

At roughly 2–4 KB of JSON per document plus one embedding vector (size depends on the embedding model, OPEN-W18), that is on the order of **tens of MB, ≤ ~100 MB**. This is an estimate, not a measurement. It fits on the head computer (Jetson Orin Nano Super class recommended in [D §3.3]).

---

## 4. Safe transition graph

### 4.1 Edge record

```json
{
  "schema_version": "bod.edge.v1",
  "transition_id": "BT1-<sha256(canonical edge minus id)[:16]>",
  "from_pose_id": "BP1-…", "to_pose_id": "BP1-…",
  "body_profile": "classic31_v1", "calibration_id": "…",
  "waypoints": ["BP1-…"],
  "channel_order": [["rightHand.*","leftHand.*"], ["rightArm.shoulder"], ["rightArm.bicep","rightArm.rotate"]],
  "max_joint_velocity_dps": {"default": 25, "rightHand.*": 50},
  "collision_class": "C0|C1|C2|CU",
  "balance_ok": true,
  "max_tip_risk_along_path": "low|med",
  "fault_preconditions": {"require_ok": ["rightArm"], "forbid_status": {"rightArm.*": ["stuck","disconnected","offline","unknown","absent"]}},
  "expected_duration_s": 3.6,
  "is_failsafe_sink": false,
  "provenance": "library|generated|operator_taught"
}
```

**ID prefix.** `BT1-` (stored under `Ax-BOD-EDGE`) is the proposed spelling, **pending maintainer approval** (OPEN-W23); v0.2 used `WT1-`. No `BT1-` ID has been minted.

**Velocity.** The Nixie start script sets `setSpeed(50)` on arms, head and torso, and `setSpeed(100)` on fingers and jaw [REPO:inmoov2]. The legacy `MAX_VELOCITY=-1` means "max" [D §1.6]. BOD v1 caps hardware edges at **50 % of the configured speed** (PLACEHOLDER). On `weak` channels it caps them at 25 %. `expected_duration_s` = max over channels of |Δq| / v, plus margin. The same figure feeds the TimeEncoder expectation in §5.

**Collision classes.**

| Class | Meaning | Allowed on hardware? |
|---|---|---|
| C0 | No plausible contact | yes |
| C1 | Proximity: hand near the other hand, torso or face. Reduced speed, operator present | yes, with the C1 speed cap |
| C2 | Known or likely self-collision | no |
| CU | Not yet classified | no (sim only) |

v1 has no geometric collision model. Classes are assigned by rule table plus visual review in JME. Whether the MRL JME simulator can detect collisions is unverified (⚠ OPEN-W7).

### 4.2 Pruning rules (an edge is invalid if any holds)

1. It commands a channel whose servo status is `stuck`, `disconnected`, `offline`, `unknown` or `absent`. Fail-safe sinks are the exception.
2. It commands a paired channel (rollNeck, topStom, midStom) while either servo in the pair is non-`ok`.
3. `to_pose`, or any waypoint, has `tip_risk = high` or `motion_allowed = false`.
4. `collision_class` ∈ {C2, CU} on hardware.
5. The target `q` is outside the channel's input limits for the current `calibration_id`.
6. A `weak` channel moves against gravity while the hand on the same side carries a load (contact ≥ 2).
7. The edge's `calibration_id` ≠ the current `calibration_id`. A stale library is never executed.

**Ordering heuristics** baked into `channel_order` (PLACEHOLDERS, to be tuned in sim):
- Fingers first when releasing.
- Retract the bicep before rotating.
- Move the torso only with both arms in `tucked`.
- Move the head independently of the arms.

### 4.3 Fail-safe sinks

Sinks are reachable from **every** state. Their edges have no fault preconditions.

| Sink | Action | When | Caveat |
|---|---|---|---|
| `S-FREEZE` | `stop()` all channels, stay energised | default on verify mismatch or LLM/validator error | a holding servo under load heats or stalls. Time-limit it, then fall to S-REST or S-LIMP |
| `S-DISABLE-ONE` | `stop()` + `disable()` on the faulted channel only | stuck or stalled joint; finger over-pressure, mirroring `J_SensorFinger` behaviour [D §1.7] | the disabled joint may sag |
| `S-REST-SLOW` | move to `rest` at 25 % speed | only if every channel used is `ok` and balance is ok | open-loop: still unverified |
| `S-LIMP` | `disable()` all, or the trainer hits E-stop SW1 | E-stop, power fault, unknown state with a person nearby | gravity-loaded joints drop. Safe only if the trainer judges it so. The hardware E-stop is the authority [D §4.5] |
| `S-POWERDOWN` | InMoov2 FSM `power_down` (`gestures/power_down.py` → `sleepModeSleep()`) [D §1.2] | end of session | |
| `S-KNEEL/BRACE` | — | not modelled for the classic robot (no legs) | reserved |

### 4.4 How the LLM proposes a path

1. **Context in.** BOD injects a compact current-state chunk (§6.1) into the LLM prompt. It contains the `pose_id`, the fault summary, the balance class and the confidence.
2. **Retrieve goals.** The LLM's request is turned into a RAG query with **hard metadata filters**: `body_profile`, `calibration_id`, compatible `fault_digest`/`limb_ok_bits`, and `motion_allowed = true`. The top-k candidate goal poses are returned.
3. **Search.** A deterministic planner runs Dijkstra/A* over stored edges plus on-demand neighbours, from the current `pose_id` to each candidate. Cost = duration + risk penalty (C1, med tip). It returns up to 3 paths.
4. **LLM choice.** The LLM picks a path and explains it in crew-facing language. It may *propose* its own path of retrieved IDs, but that path goes through the same validator.
5. **Validator (deterministic code, not the LLM).**
   - recompute every `joint_checksum` from its canonical payload
   - confirm every edge exists or is regenerated identically
   - re-check §4.2 against the *live* fault mask
   - recompute the `sequence_checksum` chain (§8)
   - any failure → reject; the LLM gets the reason; nothing moves
6. **Execute** step by step with verify (§5, §8).

---

## 5. Always-know-where-you-are loop

```
 ┌─► sense/estimate ─► PoseState update ─► quantize + checksum ─► RAG upsert (on change)
 │                                                                    │
 │   verify ◄── execute (one edge) ◄── validate ◄── plan (LLM + planner)
 │     │
 │     └─ mismatch? ─► fail-safe sink + Tx-BOD-MISMATCH log ─┐
 └────────────────────────────────────────────────────────────┘
```

| Stage | What happens | Rate / note |
|---|---|---|
| sense/estimate | Read MRL `currentInputPos` per servo, plus servo enabled/energised state, Arduino link state, rail/E-stop inputs (if wired), fingertip sensor levels, ultrasonic distance | MRL TimeEncoder samples every **200 ms** [D §1.5]. Finger sensors are polled at **2 Hz** in `J_SensorFinger.py` [D §1.7] |
| PoseState update | Fill `commanded`/`estimated`/`sensed`, status and confidence per joint, then recompute balance | each sample |
| checksum | Recompute `joint_checksum` and `pose_id` | each sample (cheap) |
| RAG upsert | Upsert only when `pose_id` or the fault mask changes. Every sample goes to an append-only **observation log** (time series, not embedded) | avoids embedding at 5 Hz |
| plan / validate | §4.4 | on request |
| execute | One edge at a time through MRL `moveTo`/`moveHand`/… with speed set | |
| verify | Compare the expected `pose_id`/`joint_checksum` with the post-step state, using whatever evidence exists (table below) | after `expected_duration_s` + margin |

### 5.1 The InMoov open-loop gap

**What stock InMoov really knows** [D §1.5]:
- MRL's default `TimeEncoder` **estimates** position from commanded speed × elapsed time. It needs no feedback from the Arduino.
- Once a move's expected time has passed, `estimated_deg == commanded_deg` **by construction**.
- The hacked HS-805BB pots (bicep, rotate, shoulder; omoplate unclear, OPEN-Q20) feed only the servo's internal loop. MRL never sees them.

So, on stock hardware:
- BOD stores those joints as **`provenance: estimated`**, never `sensed`.
- A "verify" step **cannot detect** a gear slip, a stall or an external push by comparing angles. It would compare the model against itself.
- On stock hardware, verify checks only what can actually be observed:
  - the move completed in time with no MRL error
  - the serial link is alive
  - the rail and E-stop are in the expected state
  - fingertip contact is consistent with the expectation (below)
  - an optional camera or operator confirmation
- Confidence is capped accordingly.

**Boot is unknown.** `InMoov2FullStart.py` calls `setPosition()` before moving, and its own comment says: "in theory this is the position of the servo when this file was created" [REPO:inmoov2]. At boot BOD therefore marks every joint `status: unknown`, with `q_deg = null` and the last saved state as a prior only. The first motion is a supervised, slow **homing move to `rest`**. Only after it does provenance become `estimated`.

**autoDisable → unknown (v0.3 rule, design review §3.2).** With `autoDisable` on, servos are de-energised after `idleTimeout` (default **3000 ms**) [D §1.4]. A de-energised, gravity-loaded joint may back-drive. Whether the HS-805BB joints actually sag, and by how much, is **unmeasured** (⚠ OPEN-W14). Because the drop cannot be observed on stock hardware, BOD does not merely lower confidence:
- When autoDisable de-energises a **gravity joint** (bicep, shoulder, omoplate, neck, topStom, midStom), BOD sets that joint to **`status: unknown`, `q_deg = null`**, `provenance: unknown`, `confidence: 0.0`, and its fault-mask digit(s) to `4`. The last estimate may be kept in `estimated_deg` as a prior only; it is not in the hash. The `pose_id` changes accordingly (§2.4 rule 3).
- The joint stays `unknown` **until re-homed**: a supervised, slow homing move to a known pose, the same procedure as at boot. Merely re-energising (re-asserting the last command) does **not** restore a known `q_deg`, because the joint may have moved while limp.
- BOD adds the modifier `autodisabled_sag_risk` for the event. Any `unknown` gravity joint makes CoM `unknown` (§2.6), and §4.2 rule 1 prunes every edge on it except the homing move and the fail-safe sinks.
- **Policy:** during plans, keep autoDisable **off** on gravity joints, with a time/thermal cap (⚠ OPEN-W14), so they do not drop into `unknown` mid-plan. Examples E1, E5 and E6 model this policy (gravity joints held energised).
- **Doc vs. hardware default (⚠ OPEN-W24):** holding gravity joints energised in the classic31 examples is for documentation only; it must not become the hardware default. Real rest is arms-down on hard stops so power can be cut safely (joints then `unknown` until re-homed).
- Non-gravity channels (fingers, wrist, rotate, rothead, jaw, eyes) may autoDisable without becoming `unknown`; they stay `estimated`.
- A joint deliberately disabled because it is `stuck` keeps `status: stuck` and `q_deg = stuck_at` (§7.1); a contact-stop finger keeps its estimate (§5.2).

**Confidence model** (all PLACEHOLDER, ⚠ OPEN-W4):

| Evidence | `confidence` |
|---|---|
| move in progress (commanded only) | 0.3 |
| TimeEncoder, move complete, energised | 0.6 |
| gravity-loaded joint after autoDisable (status `unknown` until re-homed, v0.3) | 0.0 |
| suspected slip / operator-reported discrepancy | 0.2 |
| unknown | 0.0 |
| real encoder reading (future) | 0.9 |
| fingertip contact corroborating a finger | +0.1, capped at 0.7 without an encoder |

### 5.2 Partial closed loop: fingertip sensors

Fingertip Hall (AH3503) or foam sensors read into Mega analog inputs [D §1.7, §8.5: thumb…pinky = A0–A4 = MRL 54–58]. `J_SensorFinger.py` maps them to pressure levels 0–3 and calls `stop()` + `disable()` on contact. Its thresholds 544/545/547/550 must be re-measured on our hardware (OPEN-Q3).

What BOD uses them for:
- **Contact-stop:** after a stop, the finger's `estimated_deg` is the TimeEncoder value *at stop time*, corroborated by contact (example E7).
- **Mismatch triggers:**
  - closing on an expected object reaches the commanded angle with no contact → grasp miss
  - contact far earlier than expected → obstruction
- **Load inference:** contact ≥ 2 on a closed hand → `asymmetric_load` modifier → balance input.

Fingertip sensors say nothing about wrist, arm, head or torso angles.

### 5.3 Upgrade path to `sensed`

| Option | Status | BOD effect |
|---|---|---|
| AS5048A or AMT203 encoder per joint | MRLComm already includes `MrlAs5048AEncoder` / `MrlAmt203Encoder` [D §1.5]. Mounting, pins and per-joint fit are not designed | joint `sensed_deg` filled, `provenance: sensed`, `encoder: As5048A/Amt203`. Verify becomes a real comparison: \|expected − sensed\| ≤ tolerance |
| Tap the extracted joint pot of the hacked HS-805BB with a spare analog input | **unverified idea**: electrical interaction with the servo's own board is unknown (⚠ OPEN-W3) | `encoder: pot_tap` |
| Per-rail current sensing (INA219 / INA226 class) | not in the dossier; a proposal only. Candidate parts listed in `parts_acquisition.csv` rows B-44/B-45 (v0.3); no wiring, firmware or overcurrent sink designed | stall/stuck detection; not position |

Priority for encoders, when budgeted: the gravity-loaded HS-805BB joints first (shoulder, bicep, omoplate, then neck and topStom). Those are where open-loop error matters most for balance and pinch safety.

---

## 6. RAG schema and Palace/NOVA hook

### 6.1 Document chunk shape

Each indexed document has three parts: **(a)** a short natural-language text that gets embedded, **(b)** structured metadata used as hard filters, and **(c)** the full JSON record stored verbatim (not embedded).

**(a) Embedded text template (PoseState):**
```text
BOD POSE {pose_name|unnamed} [{pose_id}] profile={body_profile} cal={calibration_id}
Body: right hand {shape}, wrist {q}; right arm {arm_subpose} (shoulder {q}, bicep {q}); left …; head gaze {gaze}; torso {torso_subpose}.
Health: {limb roll-up words}; modifiers: {list or none}. E-stop {state}.
Balance: CoM {com_class}, tip risk {tip_risk}, motion {allowed|NOT allowed} because {reasons}.
Confidence {x} ({provenance}, {encoder}).
Allowed next: {to_pose names of valid edges, ≤ 8}. Fail-safe: S-FREEZE, S-LIMP.
```
Edges get a similar text ("from X to Y, waypoints…, collision C0, preconditions…"). MotionPlans get one line per step.

**(b) Metadata filters:**

| Key | Type | Use |
|---|---|---|
| `doc_type` | `pose` / `subpose` / `edge` / `plan` / `fault_profile` / `mismatch` | |
| `body_profile`, `calibration_id` | string | **hard filter**: never mix |
| `pose_id`, `from_pose_id`, `to_pose_id` | string | graph joins |
| `fault_mask`, `fault_digest`, `limb_ok_bits` | string / string / int(6 bits) | compatibility filter |
| `subassembly` | `rightHand` … `torso` / `full` | sub-pose lookup |
| `tip_risk`, `com_class`, `motion_allowed` | enum / enum / bool | safety filter |
| `collision_class` | enum | edges |
| `provenance`, `confidence` | enum / float | rank down low-confidence states |
| `source_gesture` | e.g. `gestures/ready.py` | traceability |
| `content_hash` | = `record_checksum` | dedup |

### 6.2 Fit to NOVA / Palace naming (BOD keys approved 2026-10-13)

Neither the dossier nor `PROJECT_NOTES.md` mentions NOVA, Palace or `force_class`. The conventions below come from NOVA code and reports (NOVA is a separate system, not part of this repo). They were read only; nothing was changed.

- In NOVA, **"WHI" names the NOVA addressing scheme (proposed)**: a wing + subject code. Facts are keyed with a wing prefix `Ax-` / `Tx-` / `0x-`.
  - Existing keys include `Tx-HW`, `Tx-HAND`, `Tx-CAM`, `Tx-EAR`, …
  - **Name collision resolved:** the embodiment system is now **BOD**; "WHI" means only the NOVA index (OPEN-W16 naming part closed 2026-10-13).
- `Ax-` = accepted, validated AI-internal knowledge; `Tx-` = temporary staging pending promotion (per the v0.1 design review (not included) §1).
- `REPORT_RAG_ALIGNMENT_2026-09-27.md` states that **`force_class` is not implemented in live modules**. It proposes `force_class=<hexcode>` from `whi.claw_hexclass()`. `nova/hexclass_table.json` (49 entries) has **no engineering, robotics or technology code**.

**Approved fact keys (the maintainers, 2026-10-13).** Approval covers the *names only*. **No NOVA.db write is made or scheduled**: writes stay held until the deterministic validator/executor has its own PROOF (unit tests on disk), and any later mirror is a batch, signed-off job.

| Key | Wing meaning | Content |
|---|---|---|
| `Ax-BOD-POSE` | validated library | PoseState library documents (`bod.pose.v1`, `BP1-` IDs) |
| `Ax-BOD-SUBPOSE` | validated library | sub-pose library |
| `Ax-BOD-EDGE` | validated library | transition edges (`bod.edge.v1`) |
| `Ax-BOD-FAULT` | validated library | fault profiles / modifier catalogue |
| `Ax-BOD-CAL` | validated library | calibration register snapshot ↔ `calibration_id` |
| `Tx-BOD-PLAN` | staging | MotionPlans (`bod.plan.v1`, executed or rejected) |
| `Tx-BOD-MISMATCH` | staging | verify failures + the sink taken |
| *(no NOVA key)* | — | **Observation / 5 Hz pose stream (BOD-OBS): stays OUT of NOVA.** Local store only — an append-only time series in the separate BOD store on the head computer (SQLite WAL + local vector index, path from config/env). There is no NOVA write path for it and none is to be built |

Promotion of `Tx-BOD-PLAN` / `Tx-BOD-MISMATCH` records after operator acknowledgement (proposed) is **not yet specified**; ⚠ OPEN (§10.1).

Body header, following the stamped-header pattern in the alignment report (`whi=` here is the NOVA index field, not the embodiment system):

```text
whi=Ax-BOD-POSE
force_class=<PLACEHOLDER>
content_hash=<record_checksum>
pose_id=BP1-…
body_profile=…
calibration_id=…
```

`force_class` remains a **PLACEHOLDER** until current sensing exists and a hexclass code for embodiment/robotics is added by maintainer decision (not by an agent).

**This spec does not write to NOVA.db and plans no write.** The v1 design is a self-contained BOD store on the robot's head computer that starts and runs with NOVA absent. NOVA would get optional, signed-off, batch summary mirroring later, only after validator/executor PROOF.

**Separate BOD store: requirements (v0.3, design review §2 / §4.6).** The store holds live pose state, the BOD-OBS log, the library candidates, plan/mismatch staging and the local vector index. Its path comes from config/env only; there are no path literals in code. No store schema has been written and nothing is built.

- **S1 — Must run with NOVA absent.** The BOD store, validator and executor must start, run, plan, execute and fail safe with NOVA.db / Palace missing, unmounted or unreachable. No code path may block on, wait for, retry for or require NOVA. Any later mirror is optional, batch, signed-off and outbound-only. An acceptance test runs the full stack with the NOVA path unset, and it must pass.
- **S2 — Pose state must not live on a removable drive.** The live store must be on the head computer's internal, non-removable storage (e.g. an internal NVMe SSD). It must not be on a USB stick, an SD card, an external/Thunderbolt drive or a network share. Unplugging such a volume mid-plan would make the robot's self-state vanish. The executor must refuse to start if the configured path resolves to a removable or network volume (detection method is platform-specific, TBD). This is separate from where NOVA.db itself lives (OPEN-W17).
- **S3 — Retention / rotation** (all numbers are **PLACEHOLDER**, ⚠ OPEN-W17):
  - Sizing estimate (not a measurement): BOD-OBS at 5 Hz × ~3 KB/sample ≈ **1.3 GB/day** uncompressed.
  - BOD-OBS raw samples: append-only SQLite WAL, rotated into daily segments (or 256 MB, whichever comes first). Keep **7 days** on-device.
  - Older OBS: keep only change events (`pose_id` / fault-mask transitions), plus a ±60 s raw window around every `Tx-BOD-MISMATCH`, abort or E-stop. Keep these for **180 days**, then delete.
  - Never purged:
    - samples referenced by an unacknowledged mismatch or an open plan
    - mismatch records
    - the plan log of any executed plan, until the operator acknowledges it and (if ever) it is mirrored
  - Libraries: a library whose `calibration_id` has been superseded is archived read-only, not deleted.
  - Disk guard: if the store passes a disk-usage ceiling, BOD refuses new plans (fail-safe). It does not silently drop data.
  - Purge is a separate, logged maintenance job. It never runs during an active plan.

---

### 6.3 Store implementation decisions (design review 2026-10-13)

Rules the sim store (`bod_store/`, IDs via `bod_store/bod_ids.py` per §2.8) applies when it changes a record:

1. **weak** (digit 1) sets the joint's position (`q_deg`) to null.
2. **stall / stuck** (digit 2) keeps the position (the stuck-at angle).
3. **Unknown fault types** get code 3 (disconnected).
4. **Per-limb health summaries** are not recomputed after a change, because they are not part of the ID (§2.8 canonical payload).

## 7. Malfunction / disrepair profiles

### 7.1 Modifier catalogue

| Modifier | Detectable on stock InMoov by | State change | Balance effect | Graph pruning |
|---|---|---|---|---|
| `servo_dead` | No movement and no load, seen by operator or camera; MRL/Arduino error; half-torque behaviour of a pair | status 3 (disconnected); paired channel → 1 (weak) | CoM `unknown` if gravity-loaded | every edge commanding that channel; for a pair, every edge on the shared channel |
| `gear_slip` (command ≠ actual) | **Not by angle comparison** (TimeEncoder echoes the command). Only operator or camera, finger-contact anomalies, or a future encoder | status 1 (weak), `q_deg = null`, confidence 0.2 | `med` at least; `motion_allowed = false` until the operator confirms | channel excluded until re-homed |
| `limp_limb` | Rail off (REG2 etc.), `disable()`, E-stop | status 5 (offline) on the limb's servos, `q_deg = null` | CoM `unknown` → `tip_risk ≥ med` | all edges on that limb; torso edges |
| `asymmetric_load` | Fingertip contact ≥ 2 on a closed hand; operator entry of payload | modifier only (angles unchanged) | lateral or forward shift → `med` | no raise above `mid` on that side; velocity × 0.5 |
| `stuck_joint` | Commanded move with no observed motion; contact-stop that never clears; overheating | status 2 (stuck), `q_deg = stuck_at` | depends on angle | every edge moving that channel; the servo is disabled to stop stall heating |
| `autodisabled_sag_risk` | Time since de-energise > `idleTimeout` | gravity joints → status 4 (`unknown`), `q_deg = null`, confidence 0.0 until re-homed (v0.3, §5.1); non-gravity channels unchanged | CoM `unknown` if any gravity joint unknown (§2.6) | every edge on the affected gravity channels except the homing move and sinks; re-home before any plan (re-assert alone is not enough) |
| `sensor_fault` | Finger sensor stuck at one level / out of range | contact = null | loses load inference | grasp edges need operator confirmation |
| `calibration_stale` | `calibration_id` ≠ current register | whole library invalid | — | **everything except sinks** |

### 7.2 Example PoseStates

All eight are full records in `bod_pose_state.examples.json`. They **validate against `bod_pose_state.schema.json`**, and their checksums were computed with the §2.8 rule (re-verified by the independent `tools/bod_validate.py`). Below they are abbreviated: `joint_vector` and payloads are shortened. The `pose_id`s are real.

*v0.3:* in E1–E6 (`classic31_v1`) `head.rollNeck` and `torso.midStom` are `absent` (`null`, fault digits SVO23/24 and SVO30/31 = `6`) pending OPEN-W9, so all six IDs were re-minted. E7 and E8 are unchanged. The v0.2→v0.3 ID map is in `archive/bod_idmap_v0.2_to_v0.3.json`.

**E1 — `rest`, healthy; gravity joints held energised (autoDisable off on gravity joints, §5.1); non-gravity channels autoDisabled**
*Note:* energised gravity joints here are a documentation convenience only — not the hardware default (⚠ OPEN-W24).
```json
{"pose_id":"BP1-ddcab2230df85dcc","body_profile":"classic31_v1","pose_name":"rest",
 "fault_mask":"0000000000000000000000660000066",
 "joints_q":{"*Hand.fingers":0,"*Hand.wrist":90,"*Arm.bicep":0,"*Arm.rotate":90,"*Arm.shoulder":30,"*Arm.omoplate":10,"head.{neck,rothead,eyeX,eyeY}":90,"head.jaw":0,"head.rollNeck":null,"torso.topStom":90,"torso.midStom":null},
 "health":{"modifiers":[],"estop":"released"},
 "balance_profile":{"com_class":"centered","tip_risk":"low","motion_allowed":true},
 "provenance":{"overall":"estimated","confidence":0.6,"encoder":"TimeEncoder"}}
```

**E2 — `ready` (from `gestures/ready.py`)**
```json
{"pose_id":"BP1-fbc3f1b50533bda4","pose_name":"ready","fault_mask":"0000000000000000000000660000066",
 "joints_q":{"leftArm":[65,90,75,10],"rightArm":[20,80,25,20],"leftHand":[130,180,180,180,180,90],"rightHand":[50,90,90,90,100,90],"head.neck":90,"head.rothead":90},
 "balance_profile":{"com_class":"centered","tip_risk":"low","motion_allowed":true},
 "provenance":{"overall":"estimated","confidence":0.6},
 "notes":"topStom/jaw/eyes not commanded by ready.py → assumed at rest; rollNeck/midStom absent (OPEN-W9)"}
```

**E3 — `ready+RA_offline` (limp limb: REG2 arm rail off)**
```json
{"pose_id":"BP1-c74e1f42fdf1e1f8","fault_mask":"0000005555000000000000660000066",
 "joints_q":{"rightArm.bicep":null,"rightArm.rotate":null,"rightArm.shoulder":null,"rightArm.omoplate":null,"...":"rest as E2"},
 "health":{"limbs":{"rightArm":"offline"},"modifiers":[{"kind":"limp_limb","target":"rightArm"}],"power_rails":{"REG1":"on","REG2":"off"}},
 "balance_profile":{"com_class":"unknown","tip_risk":"med","motion_allowed":true,
   "reasons":["right arm unpowered, position unknown","left side + head only; no right-arm or torso edges"]}}
```

**E4 — `point_center_right` attempted, right-shoulder gear slip**
```json
{"pose_id":"BP1-097be49a6a60666c","fault_mask":"0000000010000000000000660000066",
 "joints":{"rightArm.shoulder":{"commanded_deg":58,"estimated_deg":58,"q_deg":null,"status":"weak","confidence":0.2}},
 "joints_q":{"rightArm":[70,70,null,25],"rightHand":[180,0,180,180,180,180]},
 "health":{"modifiers":[{"kind":"gear_slip","target":"rightArm.shoulder","delta_deg":null,
   "note":"TimeEncoder reports 58 = commanded; operator/camera saw arm lower. Delta unmeasurable without a sensor."}]},
 "balance_profile":{"com_class":"forward","tip_risk":"med","motion_allowed":false},
 "provenance":{"overall":"estimated","confidence":0.2}}
```

**E5 — `rest+LB_stuck45` (stuck joint)**
```json
{"pose_id":"BP1-2662c075d66574ae","fault_mask":"0000000000000000200000660000066",
 "joints":{"leftArm.bicep":{"q_deg":45,"status":"stuck"}},
 "health":{"modifiers":[{"kind":"stuck_joint","target":"leftArm.bicep","stuck_at_deg":45}]},
 "balance_profile":{"com_class":"centered","tip_risk":"low","motion_allowed":true,
   "reasons":["all edges touching leftArm.bicep pruned"]}}
```

**E6 — `rest+topStom_B_dead` (servo dead, half of a paired channel)** *(v0.3: re-targeted from midStom, which is now `absent`; a midStom fault would have collided with E1's pose_id)*
```json
{"pose_id":"BP1-5475e1f26c408f54","fault_mask":"0000000000000000000000660001366",
 "joints":{"torso.topStom":{"q_deg":90,"status":"weak"},"torso.midStom":{"q_deg":null,"status":"absent"}},
 "health":{"limbs":{"torso":"weak"},"modifiers":[{"kind":"servo_dead","target":"SVO29"}]},
 "balance_profile":{"tip_risk":"med","motion_allowed":false,"reasons":["torso drive degraded","no torso motion; arm motion only with reach <= mid"]},
 "notes":"Joint vector alone cannot express this; fault mask (per physical servo) can."}
```

**E7 — Finger Starter contact-stop (`finger_starter_v1`)**
```json
{"pose_id":"BP1-ba6f827684a0cc16","body_profile":"finger_starter_v1",
 "calibration_id":"cal-fingerstarter-testscript-map-0-180-42-160","fault_mask":"0",
 "joint_vector":[{"name":"rightHand.index","svo":["SVO00"],"commanded_deg":180,"estimated_deg":130,"q_deg":130,
   "sensed_deg":null,"provenance":"estimated","confidence":0.5,"status":"ok","energized":false}],
 "contact":{"rightHand.index":2},
 "balance_profile":{"tip_risk":"low","support_note":"FingerTesterV2 base on bench"}}
```

**E8 — Right arm on bench, holding an object (`right_arm_v1`, asymmetric load)**
```json
{"pose_id":"BP1-968efdbe025b4664","body_profile":"right_arm_v1","fault_mask":"0000000000",
 "joints_q":{"rightArm":[45,90,60,10],"rightHand":[120,120,120,120,120,90]},
 "contact":{"rightHand.thumb":2,"rightHand.index":3,"rightHand.majeure":2,"rightHand.ringFinger":2,"rightHand.pinky":0},
 "health":{"modifiers":[{"kind":"asymmetric_load","target":"rightHand","note":"object mass unknown"}]},
 "balance_profile":{"com_class":"forward","tip_risk":"med","motion_allowed":true,
   "reasons":["payload in hand at mid reach","max velocity halved","no shoulder raise above 90 while loaded"]}}
```

E8's angles are illustrative. They are not from a gesture file.

---

## 8. Sequence plan and checksums

### 8.1 MotionPlan

```json
{
  "schema_version": "bod.plan.v1",
  "plan_id": "BPL1-<derivation TBD>",
  "body_profile": "classic31_v1", "calibration_id": "…",
  "requested_by": "llm|operator", "request_text": "…",
  "start": {"pose_id": "BP1-…", "joint_checksum": "<64 hex>", "fault_mask": "…"},
  "steps": [
    {"i": 1, "transition_id": "BT1-…", "expected_pose_id": "BP1-…", "expected_joint_checksum": "<64 hex>",
     "sequence_checksum": "<64 hex>", "timeout_s": 6.0, "verify": ["time_complete","no_mrl_error","rails_ok","contact_expect:none"]}
  ],
  "goal": {"pose_id": "BP1-…", "joint_checksum": "<64 hex>", "goal_sequence_checksum": "<64 hex = last step>"},
  "abort_conditions": ["estop_engaged","fault_mask_changed","verify_mismatch","timeout","balance_high",
                       "link_lost","operator_stop","calibration_changed","unexpected_contact"],
  "on_abort": "S-FREEZE then operator decision (S-REST-SLOW | S-LIMP)",
  "validator": {"version": "…", "result": "accepted|rejected", "reasons": []}
}
```

**ID prefixes (proposed, pending maintainer approval, OPEN-W23):** plans `BPL1-` (staged under `Tx-BOD-PLAN`), transitions `BT1-` (under `Ax-BOD-EDGE`). v0.2 used `MP-` / `WT1-`. The `BPL1-` derivation (what is hashed) is not defined yet and no `BPL1-` ID has been minted. The required `evidence_level` field is still missing from this template (§10.1, OPEN).

### 8.2 Sequence checksum chain

```
s0 = SHA256( canonical({"v":"bod.seq.v1","plan_id":P,"start":start_joint_checksum}) )
si = SHA256( s(i-1) + ":" + full_transition_hash_i + ":" + expected_joint_checksum_i )   (ASCII hex, ':' separators)
goal_sequence_checksum = s_n
```

`full_transition_hash_i` = SHA-256 of the canonical edge record, minus its ID. The chain therefore binds:
- the start state,
- every edge's exact content (waypoints, speeds, preconditions),
- every expected intermediate state, and
- the order.

If any step is changed, swapped or skipped, `s_n` changes. The plan is not executable until the validator's recomputed `s_n` equals the stored one.

**Worked example (real values for poses and `s0`).** `rest` → `rest_hands_ready` (hands only) → `ready`, demo plan label `MP-demo-rest-to-ready` (kept as the literal hashed into `s0`; no `BPL1-` ID exists yet):

| Step | Pose | joint_checksum (first 16) | Transition | s_i |
|---|---|---|---|---|
| 0 | `rest` BP1-ddcab2230df85dcc | ddcab2230df85dcc… | — | `0b6b57ca0cf4d920…` |
| 1 | `rest_hands_ready` BP1-afe0c86297eee0fd | afe0c86297eee0fd… | *BT1-… (proposed prefix, pending approval; hands, C0, 50 °/s) — ID WITHDRAWN, see note* | *withdrawn* |
| 2 | `ready` BP1-fbc3f1b50533bda4 | fbc3f1b50533bda4… | *BT1-… (proposed prefix, pending approval; arms+head, C1, 30 °/s) — ID WITHDRAWN, see note* | *withdrawn* |

**Regen note (v0.2, 2026-10-13; values superseded by the v0.3 note below).** Step 0 (`s0`) and all three pose IDs were recomputed for `bod.seq.v1` / `bod.pose.v1` (`rest_hands_ready` = `rest` with every hand channel at its `ready` value; that definition reproduces the v0.1 ID `WP1-0c5e9c6d946c6a1e` under the old version string, so it is the same pose). The v0.1 transition IDs (`WT1-2955b84f…`, `WT1-3991e412…`), `s1` and the goal `s_n` are **withdrawn**: they hash full edge records that embed the old `whi.edge.v1` string and `WP1-` IDs, and those two demo edge records were not saved in the pack, so they cannot be honestly regenerated here. They will be re-minted by the deterministic validator from stored `bod.edge.v1` records (§9 phase 0). The `WT1-` transition prefix is unchanged pending a naming decision (not in the approved BOD map; ⚠ OPEN-W23).

**Regen note (v0.3, 2026-10-13).** The three pose IDs and `s0` were recomputed because `rest`/`ready` now carry rollNeck/midStom `absent` (`rest_hands_ready` uses the same definition as v0.2, which reproduces `BP1-8330ec88e6c255f9` on the v0.2 data). Full `s0` = `0b6b57ca0cf4d920df9f6919eb10a370f79f2adf38063ec613154e20d4953490` (v0.2 `667e4bda…` retired). Transition cells show design review's proposed `BT1-` spelling, marked pending.

> **NOTE — chain rebuild on hold.** The §8.2 chain (transition IDs, `s1` … `s_n`, goal checksum) is **not** rebuilt in v0.3. It is rebuilt only after **the maintainers approve `BT1-` / `BPL1-`** (OPEN-W23). It will then be minted by the deterministic validator from stored `bod.edge.v1` records. No `BT1-`/`BPL1-` hash has been invented here.

### 8.3 Per-step execution and verify

1. **Pre-check.** The live `joint_checksum` must equal the step's *from* checksum (`start`, or the previous step's expected checksum). The live fault mask must be unchanged.
2. **Execute** the edge's `channel_order` groups at the edge's speeds.
3. **Wait** `expected_duration_s`, up to `timeout_s`.
4. **Build** the post-step PoseState and recompute `joint_checksum`.
5. **Compare** it with `expected_joint_checksum`, and run every `verify` check the current sensor level supports (§5.1).
6. **Match** → append the step result to the plan log and continue.
   **Mismatch** → `S-FREEZE`, write a `Tx-BOD-MISMATCH` record (expected vs. observed payloads, evidence, sink taken), mark the plan `aborted`, and require operator acknowledgement before any new plan.
7. **Goal reached** → verify the final `joint_checksum` == `goal.joint_checksum` and the chain `s_n` == `goal_sequence_checksum`.

On stock hardware, step 5's angle comparison is necessary but not sufficient, because estimates echo commands. A plan log must state the evidence level it actually achieved (`time_only`, `contact`, `sensed`).

---

## 9. MVP build order

These phases follow the dossier's "sim first, then one servo, then one subassembly at a time" workflow [D §1.8, §6.1].

| Phase | Slice | BOD deliverable | Runs where |
|---|---|---|---|
| **0 — Sim / offline** | `classic31_v1` in MRL Nixie + JME (VinMoov5), no Arduino [D §1.3, §1.8] | Canonicaliser + checksum lib; schema validation; library seeded from `rest`, `ready`, `relax`, `showObject`, `lookAtThis`; edge generator; validator; planner; MotionPlan chain; `provenance.source_system = mrl_jme_sim` | Box or lab PC. **Sim validates sequencing, limits, checksums and visual self-collision only. JME does not model torque or mass [D §1.3], so it cannot validate balance** |
| **1 — Finger Starter** | `finger_starter_v1`: 1 servo, SVO00 on Uno pin 3, external 6 V via REG0 [D §2.1, §8.5 W-FS-*] | Full state space is enumerable: 37 grid states × 7 status codes. Detents `open` 0 / `half` 90 / `closed` 180 (input; output map 42–160 in the legacy test script). All adjacent-grid edges. Sink = `S-DISABLE-ONE`. Exercise boot-unknown → homing → estimated. Optional SEN00 fingertip sensor → contact-stop (E7). Analog pin is OPEN-Q21 | MRL on dev PC/SBC + Uno |
| **2 — One arm (right)** | `right_arm_v1`: SVO01–SVO10 on ARD1, REG1/REG2 rails, SEN01–05 [D §8] | 36 hand sub-poses × 15 arm sub-poses = **540** compositions. **Fully precomputable** with checks. First real use of gravity-joint confidence, autoDisable sag measurement (OPEN-W14), `asymmetric_load` (E8). The arm must be on a rigid bench fixture | JME first with the same script, then hardware |
| 3 — Head | + neck, rothead, rollNeck, jaw, eyeX/Y | Gaze/face sub-poses; resolve rollNeck conflicts (OPEN-Q19) before the library | |
| 4 — Left arm | mirror on ARD2 (OPEN-Q28) | | |
| 5 — Torso + stand | topStom, midStom; stand geometry | Balance model v1 needs mass and stand data (OPEN-W5) | |
| 6 — Full body + LLM in the loop | `classic31_v1` | LLM tool interface: `get_state()`, `search_poses()`, `propose_plan()`. The validator and executor stay outside the LLM | Head computer (Jetson class [D §3.3]); MRL+JME on ARM64 unverified (OPEN-Q8) |

The LLM joins at the end of phase 0 in **sim only**: it plans against JME, and a human reviews every plan. It reaches hardware only after phase 2 has shown that fault pruning and fail-safe sinks work on a real arm.

---

## 10. Open questions

| ID | Question |
|---|---|
| OPEN-W1 | **Bin size.** 5° global grid (proposed) vs. per-joint bins (fingers 10°, jaw 2 detents, eyes 10°)? Detent values are placeholders |
| OPEN-W2 | **Which limbs in v1 hardware?** Proposal: Finger Starter → right arm. Is the head needed early for crew-training interaction? |
| OPEN-W3 | **Encoder upgrade path.** AS5048A vs. AMT203 (both in MRLComm), mounting per joint, budget; is the pot-tap idea electrically safe? Which joints first? |
| OPEN-W4 | **Confidence model** numbers (§5.1) are placeholders. Calibrate against camera or encoder ground truth |
| OPEN-W5 | **Stand / mount geometry, robot mass, per-part masses.** None are in the dossier. They are needed for any real CoM / support-polygon model. Lunar-g dynamic effects on velocity limits |
| OPEN-W6 | **Balance-rule thresholds** (shoulder 40/90, topStom ±20, contact ≥ 2) are placeholders |
| OPEN-W7 | **Collision model.** Does MRL JME provide collision detection? Do we need a simple capsule model per link? |
| OPEN-W8 | **`calibration_id` source of truth.** Calibration register vs. MRL YAML (dossier OPEN-Q2). Recalibration invalidates the library by design; is a regeneration workflow acceptable? |
| OPEN-W9 | **Config conflicts:** rollNeck map 60–130 vs. 20–170 and pin 12 vs. 30 (OPEN-Q19); eyeX/eyeY output ranges; midStom input 0–180 vs. 60–120. *Interim (v0.3):* rollNeck and midStom are `absent` in all `classic31_v1` records until settled in a signed calibration register (§2.2) |
| OPEN-W10 | **`torso.lowStom`** exists in the Nixie start script (pin 29) and `moveTorso` takes 3 arguments, but the dossier says the low stomach has no servo. Is a 3rd torso channel intended? Excluded from v1 |
| OPEN-W11 | **i2Head / i2Hand / eyelids** profile (OPEN-Q5): separate `i2_v1` profile? Adds ~17+ channels and changes cardinality |
| OPEN-W12 | **Paired-channel semantics.** Can the system ever tell which servo in a pair failed without per-servo current sensing? |
| OPEN-W13 | **Argument order** of `moveArm`/`moveHand`/`moveHead`/`moveTorso`: verify against the InMoov2 Java source before the library generator relies on it |
| OPEN-W14 | **autoDisable sag.** Do de-energised HS-805BB joints back-drive under gravity, and by how much? Should `autoDisable` be off for gravity joints during plans? *v0.3 rule (§5.1):* a de-energised gravity joint is `unknown` until re-homed; policy = autoDisable off on gravity joints during plans. The time/thermal cap is still open |
| OPEN-W15 | **Finger-sensor thresholds** and the maintained Nixie script (dossier OPEN-Q3); AH3503 electrical spec (OPEN-Q22) |
| OPEN-W16 | **NOVA naming — naming part CLOSED 2026-10-13** (BOD keys approved by the maintainers, §6.2). Still open: `force_class` code (PLACEHOLDER; none suitable in `hexclass_table.json`) and the go-ahead for any NOVA.db write, which stays held pending validator/executor PROOF |
| OPEN-W17 | **NOVA.db storage (concern only, no move planned).** NOVA's database lives on a separate workstation. An external drive was raised as a consideration. BOD library + embeddings are estimated at ≤ ~100 MB. A raw 5 Hz observation log grows without bound, which is why it is proposed to stay out of NOVA. *v0.3:* the BOD store's own rules are now in §6.2 S1–S3. It must run with NOVA absent. **Pose state must not live on a removable, external or Thunderbolt volume.** A retention policy is drafted, with PLACEHOLDER numbers. Still open: sign-off of the retention numbers, and where NOVA.db itself lives |
| OPEN-W18 | **On-board embedding model** (which model, vector size, RAM alongside MRL + a 3B LLM on 8 GB [D §3.3]) |
| OPEN-W19 | **Integrity vs. tamper.** Plain SHA-256 or HMAC-SHA-256 with a validator-held key? |
| OPEN-W20 | **Where the planner/validator runs** (MRL Jython service vs. a separate process on the head computer) and its latency budget |
| OPEN-W21 | **Habitat safety standard** for robot motion near crew (beyond the lithium question OPEN-Q13); who certifies the S-LIMP vs. S-FREEZE policy? |
| OPEN-W22 | **Location source** for `world` frame: fixture registry, fiducials or operator entry? |
| OPEN-W23 | **Transition / plan ID prefixes. Proposed (2026-10-13), pending maintainer approval:** transitions **`BT1-`** under **`Ax-BOD-EDGE`** (`bod.edge.v1`) and plans **`BPL1-`** under **`Tx-BOD-PLAN`** (`bod.plan.v1`), both **versioned from day one** (the `1` is the format version, as in `BP1-`). These replace v0.2's `WT1-` / `MP-`. v0.3 shows the proposed spelling in §4.1, §8.1 and §8.2, marked *pending maintainer approval*. No `BT1-`/`BPL1-` ID has been minted, and the `BPL1-` derivation is undefined. **NOTE:** the §8.2 checksum chain is not rebuilt until the maintainers approve `BT1-`/`BPL1-` |
| OPEN-W24 | **Hardware rest ≠ classic31 energised examples (design review note, 2026-10-13).** Keeping gravity joints energised in the classic31 example poses (E1/E5/E6) is fine for documentation, but must **not** become the hardware default. Hobby servos holding against gravity run hot and draw constant current. Real "rest" should be a supported pose with arms down on hard stops so power can be cut safely; joints then return as `unknown` until re-homed (§5.1). Cross-ref: §5.1 policy bullet; E1 note |

### 10.1 Design review items: status after v0.3 (from the v0.1 design review (not included) + the v0.2 design review (not included), 2026-10-13)

The v0.2 regen fixed **only** naming (design review §1 / §4.1) and schema validation PROOF (design review §4.5, see `BOD_SCHEMA_PROOF.md`). v0.3 (the v0.2 design review (not included) next pass) changes **spec text and example data only**. No code that moves anything exists. A status of "SPEC" or "PARTIAL" below means *written down*, not *built*:

| Review ref | Item | Status |
|---|---|---|
| §2 / §4.6 | Separate local BOD store (SQLite WAL + OBS log with rotation/retention); NOVA.db unacceptable for real-time pose state; store must run with NOVA absent; config path via env, no literals | PARTIAL: requirements S1 (NOVA absent), S2 (not on a removable drive) and S3 (retention, PLACEHOLDER numbers) are in §6.2 (v0.3). There is no store schema and nothing is built |
| §3.1 / §4.4 | Estimate ≠ truth: `time_only` evidence may not be labelled "verified" nor gate gravity-joint hardware motion; add required `evidence_level` to plan/step schema | OPEN (schema not yet changed) |
| §3.2 | autoDisable sag → `q_deg=null`/`unknown` until re-homed, or autoDisable off for gravity joints with thermal cap | SPEC (v0.3): the rule is in §5.1/§7.1/§2.5, the validator policy check rejects de-energised `ok` gravity joints in records, and E1/E5/E6 aligned. Executor enforcement is not built; the thermal cap is OPEN-W14 |
| §3.3 / §4.2 | Resolve rollNeck/midStom/lowStom config conflicts (W9/W10) into one signed calibration register; until then those channels `absent` in all profiles | PARTIAL: rollNeck/midStom are `absent` in all 6 classic31 examples (v0.3) and lowStom was already excluded. The signed/frozen calibration register is still OPEN; `cal-*-defaults-v0` IDs are still in use |
| §3.4 | Independent executor/firmware limit clamp (ServoConfig `clip`, per-servo min/max); LLM never emits raw degrees to the executor | OPEN |
| §3.5 | Per-rail current sensing (INA219/INA226-class) with deterministic overcurrent sink; source of `weak`/`stuck`; check `parts_acquisition.csv` | PARTIAL: candidate parts B-44 (INA219) / B-45 (INA226) added to `parts_acquisition.csv` (v0.3). Shunt sizing, wiring, firmware and the overcurrent sink are OPEN |
| §3.6 | Wire SW1 E-stop state to a sensed input; post-release all channels `unknown` until homed, enforced by executor | OPEN |
| §3.7 | MRLComm/executor link watchdog → hold then disable, status `unknown` | OPEN |
| §3.8 | Global per-joint speed cap in calibration register, executor-enforced | OPEN |
| §3.9 | Verify argument order (OPEN-W13) against InMoov2 source before generator emits `moveHand/moveArm` | OPEN |
| §3.10 | Collision model (OPEN-W7); C-class labels are placeholders; sim success ≠ hardware safe | OPEN |
| §3.11 | HMAC with validator-held key before LLM output reaches hardware (OPEN-W19) | OPEN |
| §4.3 | Deterministic validator + executor as standalone code with unit-test PROOF | OPEN (not started). `tools/bod_validate.py` is now independent of the generators, but it validates example records only; it is **not** the motion validator |
| CONFIRM §1/§5 | OPEN-W23 ID prefixes `BT1-`/`BPL1-` | OPEN (proposal recorded; awaiting maintainer approval; §8.2 chain rebuild on hold) |
| §1 | Alias rows in `whi.py` map only after sign-off; zero NOVA rows written until validator/executor PROOF | OPEN (held) |

**Cannot be known yet:**
- real tip margins
- real joint accuracy under load
- HS-805BB stall behaviour (dossier OPEN-Q11)
- whether TimeEncoder timing matches the real servos at our speeds
- real retrieval quality of a small on-board embedding model

All of these need hardware or measurements that do not exist yet.

---

## Appendix A — Dossier and repo facts relied on

- 31 physical servos / 28 MRL channels; the rollNeck, topStom and midStom pairs share channels; the servo type per joint [D §2.2].
- SVO00–SVO31 reference designators; REG0–REG6 rails; SW1 E-stop; SEN01–05 fingertip sensors [D §8.2].
- Right-side pins and fingertip analog pins A0–A4 (MRL 54–58) [D §8.5].
- Legacy default pins and head limits [D §1.6].
- `ServoConfig` fields; `map()`; 544–2400 µs; `autoDisable` `idleTimeout` 3000 ms [D §1.4].
- TimeEncoder estimate (speed × time, 200 ms sampling); optional AS5048A/AMT203 encoders in MRLComm; hacked HS-805BB pots not reported to MRL [D §1.5].
- `J_SensorFinger.py` levels 0–3 → `stop()` + `disable()`, 2 Hz polling, default thresholds 544–550 [D §1.7].
- JME VinMoov5 shows servo intent only; no torque, current or tendon model [D §1.3].
- InMoov2 FSM states, including `power_down` [D §1.2]; 223 gesture files [D §1.2].
- Low stomach is a pole mount with no servo [D §2.2, §5.2].
- Finger Starter: pin 3; `map(0,180,42,160)` in the legacy test script [D §1.4, §2.1, §8.5].
- MRL `LLM` service → Ollama; Jetson Orin Nano Super recommended; 8 GB shared memory [D §3.1, §3.3].
- Hardware E-stop is non-negotiable [D §4.5].
- From `inmoov2/system/startScripts/InMoov2FullStart.py`: per-channel `map()`, `setRest()`, `setSpeed()`, `setPin()`, `autoDisable`, the `setPosition()` "in theory" comment, and the `lowStom` definition.
- From `inmoov2/gestures/`: `rest.py`, `ready.py`, `relax.py`, `showObject.py`, `lookAtThis.py`, `armsUp.py`, `handopen.py`, `handclose.py`, `power_down.py`.
- From `inmoov/InMoov/config/skeleton_*.config.default`: legacy limits and rests.

*End of v0.3 (design-review next-pass fixes on the v0.2 BOD naming regen).*
