# BOD Decision-Trigger Catalog (InMoov body / embodiment layer)

> **Status: proposed design.** Every concept in this document (pose IDs, fact keys, ID prefixes, triggers, store layout) is a proposal. It has been exercised in simulation only and is not validated on hardware.

| Field | Value |
|---|---|
| Status | **aligned to the rewritten store** `bod_store/` (2026-10-13 20:20 CT, after OPEN-16 fix and draft calibration register; box only, sim only) |
| Time zones | The box clock runs on **Pacific time (America/Los_Angeles, PDT = UTC−7)**; file mtimes and `date` on the box read in PT. **Central time (America/Chicago, CDT = UTC−5) is the reporting zone**: every time in this catalog is CT (box time + 2 h). The store's audit and override logs stamp CT explicitly from the real clock |
| Companion to | `BOD_EMBODIMENT_SPEC.md` v0.3 (§6.3 store decisions), `bod_pose_state.schema.json` (`bod.pose.v1`), `BOD_NEXTPASS_NOTES.md`, `bod_store/TRIGGER_FIELDS.md` |
| Event names | Built in `bod_store/triggers.py`: `estop_active`, `joint_unknown`, `fault_raised`, `goal_requested`, `state_stale`, `power_cut_rehome_needed`. `current_out_of_range` and `balance_support_lost` are **DEFERRED** (no current or support sensors yet) |
| Field source | `triggers.py`, `rag_adapter.py`, `store.py`, `SCHEMA.sql`, `bod_config.json`, `estop_release.py`, `tests/test_open16.py`, `calibration/register.json` + `loader.py` + `measured.json`, cross-checked with `TRIGGER_FIELDS.md`. Example blocks below are real output from a sim run against a temp DB |
| Built? | Store, read-only RAG adapter, trigger detection and goal checks are built **in sim only** (internal proof notes, not included; 145/145 tests (re-run independently on a clean copy 2026-10-13 ~20:25 CT); OPEN-16 proof 20:09:51 CT = 18:09:51 PT box time). The deterministic validator/executor is **not** built. No NOVA.db read or write. No hardware run |

## 0. Ground rules

- **Store.** Separate SQLite file (WAL), default `./data/bod_state.db` under the `bod_store` install dir; config `db_path` in `bod_config.json`, env `BOD_DB_PATH` (also `BOD_HOME`, `BOD_CONFIG`, `BOD_EXAMPLES_PATH`, `BOD_STALE_S`). BOD never writes NOVA.db. Runs with NOVA absent (tested); must not sit on a removable drive (spec §6.2 S1–S2).
- **What the store holds** (`SCHEMA.sql`):
  - `current_state`, one row per joint: `joint`, `pose_id` (`BP1-`, §2.8 via `bod_ids.py`), `pose_name`, `body_profile`, `angle_deg` (NULL when unknown/absent/off), `confidence` (open-loop cap 0.6; measured keeps its value), `status` (`ok | unknown | absent | fault | off`), `detail` (spec status), `source` (`commanded | measured | sim`), `energized`, `gravity`, `verified_at`, `last_write_at`.
  - `status = off` is a joint switched off on purpose (spec digit 5). It is **not** a fault: no `faults` row, no `fault_raised`.
  - `verified_at` is nullable (NULL = never verified). Only a measured value (`sensed_deg`) or a rehome sets it. Open-loop sim writes keep the previous value.
  - `last_write_at` is the time of any write to the row.
  - `faults` (`joint`, `kind`, `detail`, `raised_at`, `cleared_at`), `events` (append-only; retention purge only; `ts` is always the real wall clock), `pose_library` (the 8 examples).
  - `estop_audit` (append-only, DB triggers block UPDATE/DELETE): `ts`, `when_cdt`, `operator_id`, `reason`.
  - `meta`: `pose_record` (current full record, re-minted §2.8), `rehome_required`, `estop` (`active|released`), `motion_allowed` (`1|0`).
- **E-stop latch.** `meta.estop` is latched. A loaded record (`health.estop=active`) or `SimStore.set_estop(True)` can raise it; a record saying `released`, `update_from_sim()` and `rehome()` never clear it. `set_estop(False)` raises an error. While it is active, `motion_allowed` is forced to `0` (record loads and `set_motion_allowed(True)` are coerced to false, not refused). The **only** release is the operator-only `estop_release.release_estop(operator_id, reason)`: both must be non-empty, the latch must be active, and it appends an `estop_audit` row plus an `estop_released` event. Release does **not** re-enable motion; `motion_allowed` must be set separately. `rag_adapter.py` and `triggers.py` do not import `estop_release` (AST test), and the adapter's read-only connection cannot write `meta`.
- **Intentional off.** Non-gravity `switch_off(joint)` → `status = off` (digit 5, not a fault). Gravity `switch_off(joint)` → `status = unknown` (digit 4) at once, which fires `joint_unknown`. `rehome()` keeps `off` joints off (and keeps faults). Only `switch_on(joint)` brings an `off` joint back, as `unknown` until the next rehome.
- **NOVA access (OPEN-18a).** NOVA reads ONLY the operator snapshot file at config `nova_snapshot_path` (default `./data/nova_snapshot.db`, relative to the install dir; env `BOD_SNAPSHOT_PATH`). It never opens the live db, `-wal` or `-shm`. The writer side makes the snapshot: `SimStore` writes it when it opens and after every commit (`_commit()` → `write_snapshot()`, non-WAL, atomic `os.replace`), and `estop_release` refreshes it too. Every NOVA open uses `file:…?mode=ro&immutable=1` (O_RDONLY). If the snapshot is missing, unreadable or corrupt, NOVA fails closed: `snapshot_status = snapshot_missing|snapshot_unreadable|snapshot_corrupt`, `stale=true`, `motion_allowed=false`, the block says `NO SNAPSHOT …`, `state_stale/HOLD` fires, and every goal gets `REFUSE_MOVE` with `failed_checks=[<status>]`. Each read carries `snapshot_taken_at` (file mtime) and `snapshot_age_s`. The block shows `oldest_joint_age=` and `snapshot_age=` separately, and `STALE(…, driven by oldest_joint|snapshot)` names what made it stale. It returns `body_status_block()` (≤ ~200 tokens) or `body_status_json()`.
- **R1.** The LLM proposes and deterministic code decides. Every trigger has `safe_action_overrides_llm = true`. NOVA never computes checksums, never changes `status`, never cancels a safe action.
- **R5.** E-stop SW1 overrides everything; in the store it raises `estop_active` (body scope) and stays latched until an operator release. The flag is **simulated**; there is no sensed input.
- **Row layout.** Each trigger lists: detection (as coded) → trigger record → NOVA block → what NOVA may do → deterministic action → escalation.

### 0.1 Trigger record (`triggers.detect()`)

| Field | Type | Meaning |
|---|---|---|
| `trigger_event` | str | one of the 6 built event names |
| `severity` | str | `info` / `medium` / `high` |
| `joints` | list[str] | affected channels; for `state_stale`, the stale joints |
| `reason` | str | short deterministic explanation |
| `safe_action` | str | `HOLD`, `DEENERGIZE_TO_HARD_STOPS`, `REFUSE_MOVE`, `REQUEST_REHOME`; `PASS_TO_VALIDATOR` when every goal check passes |
| `safe_action_overrides_llm` | bool | always `true` |
| `pose_id` | str\|null | current `BP1-` id (null on an empty store) |
| `nova_context_block` | str | the `body_status_block()` text |
| `goal_pose_id` | str | `goal_requested` only |
| `failed_checks` | list[str] | `goal_requested` only; empty ⇒ `PASS_TO_VALIDATOR` |
| `scope` | str | `fault_raised`, `estop_active`: `joint` / `limb` / `body` |
| `scope_limbs` | list[str] | limbs to de-energise; `[]` when `scope` is `body` or `joint` |

### 0.2 Mapping table

| trigger_event | Fires when (code) | severity | joints | safe_action |
|---|---|---|---|---|
| `estop_active` | latched `meta.estop = active` | `high` | `[]` | `DEENERGIZE_TO_HARD_STOPS`, scope `body` |
| `power_cut_rehome_needed` | `meta.rehome_required = 1` (power cut; also an empty store) | `high` | all unknown joints | `REQUEST_REHOME` |
| `joint_unknown` | any joint `status = unknown` (incl. a gravity joint switched off, or a joint just switched on) | `high` | unknown joints | `HOLD` |
| `fault_raised` | any joint `status = fault` (not `off`) | `high` | faulted joints | `DEENERGIZE_TO_HARD_STOPS`, scope per §2 |
| `state_stale` | snapshot older than `stale_after_s`, or any live (ok/fault) joint never verified or verified > `stale_after_s` ago (5.0 s) (age = older of snapshot time and joint verification), no live joints, or all unknown | `medium` | stale joints | `HOLD` |
| `goal_requested`, refused | goal given, `failed_checks` not empty | `high` | `[]` | `REFUSE_MOVE` |
| `goal_requested`, valid | goal given, `failed_checks` empty | `info` | `[]` | `PASS_TO_VALIDATOR` |

`detect()` returns them in the order e-stop → power cut → unknown → fault → stale → goal; several can fire together.

### 0.3 Body-status block (real format)

```text
[BOD body status | read-only | LLM may not command motion]
pose=<BP1-id> (<pose_name>, <body_profile>)[ [not a library pose]]
evidence=<measured | sim(open-loop commanded) | commanded(open-loop) | none> min_conf=<x> ok=<n>
unknown=<joints, first 6 then +N | none>
fault=<joint:detail,... | none>
absent=<joints | none>
off=<joints | none>
estop=<ACTIVE|released> motion_allowed=<true|false>
verified_age=<s|never>s (oldest joint) last_write_age=<s>s[ STALE(><limit>s) - do not trust pose]
[REHOME REQUIRED (power cut): no motion except supervised homing.]
[calibration: <N> unapplied overrides]
```
Empty store: the header line, then `NO STATE: store empty. Treat whole body as UNKNOWN; re-home required.`

### 0.4 Status JSON (`body_status_json()`) keys
`pose_id, pose_name, body_profile, unknown, fault, absent, off, ok_count, min_conf, evidence, age_s, stale_after_s, stale, stale_joints, oldest_verified_at, last_write_at, write_age_s, rehome_required, library_match, estop, motion_allowed`. `age_s` is measured on the **oldest** verified joint that matters (`null` if any was never verified). With no ok/fault/off joint, `pose_name = unknown` and `library_match = false`.

---

## 1. `joint_unknown`

- **Detection.** Any joint `status = unknown`, set by `mark_unknown(joints, reason)` (autoDisable of a gravity joint, or a power cut of all present joints), by `switch_off()` on a gravity joint (sim: `rightArm.shoulder` → `joint_unknown`, `joints=[rightArm.shoulder]`), or by `switch_on()` of an off joint until rehomed. Sets `angle_deg = NULL`, `confidence = 0`, `energized = 0`, digit 4, re-mints `pose_id`; faulted joints stay `fault`. `absent` channels (head.rollNeck, torso.midStom) never trigger.
- **Trigger.** `high`, `joints = <unknown>`, `reason = "joint position cannot be trusted"`, `HOLD`.
- **NOVA sees** (sim, autoDisable of `rightArm.bicep`):
  ```text
  [BOD body status | read-only | LLM may not command motion]
  pose=BP1-6920cf027b0c1c16 (rest, classic31_v1) [not a library pose]
  evidence=sim(open-loop commanded) min_conf=0.6 ok=25
  unknown=rightArm.bicep
  fault=none
  absent=head.rollNeck,torso.midStom
  off=none
  estop=released motion_allowed=true
  verified_age=0.0s (oldest joint) last_write_age=0.0s
  ```
- **NOVA may.** Explain, ask the operator to confirm the area is clear, propose homing only.
- **Deterministic action.** `HOLD`; any goal is refused (`unknown joints/rehome`). Only a rehome clears it. Edge pruning (§4.2) belongs to the unbuilt validator.
- **Escalation.** Operator ack before homing (no ack fields yet). Homing failure → `fault_raised`. Person nearby → `S-LIMP` / SW1 per §4.3.

## 2. `fault_raised`

- **Detection.** Any joint `status = fault`, from `raise_fault(joint, kind)` or a record joint with status weak/stuck/disconnected. Digits (§2.5, `bod_ids.FAULT_CODE`): weak 1, stuck/stall 2, disconnected/dead/fault 3, unknown kinds 3. Weak and other non-stuck faults null the position; stuck/stall keep it (spec §6.3). `raise_fault(kind="offline")` routes to `switch_off()` and record status `offline` loads as `off`; neither fires this trigger.
- **Trigger.** `high`, `joints = <faulted>`, `reason = "servo fault present (scope <s>)"`, `DEENERGIZE_TO_HARD_STOPS`, plus `scope` and `scope_limbs`.
- **Scope rule** (`fault_scope()`):
  - **body**: e-stop active, or any power-fault kind (`power`, `power_fault`, `brownout`, `rail`, `rail_off`); `scope_limbs = []`.
  - **joint**: only when every faulted joint is a hand finger, a hand wrist (rotation), `head.jaw`, `head.eyeX` or `head.eyeY`.
  - **limb**: everything else, including `*Arm.rotate` and `head.rothead`.
  - The widest scope wins. `scope_limbs` lists the affected limbs for `limb` scope only; it is `[]` for `joint` and `body`.
- **NOVA sees** (sim, `head.jaw` weak then `rightArm.shoulder` stall → `scope=limb`, `scope_limbs=[head, rightArm]`):
  ```text
  [BOD body status | read-only | LLM may not command motion]
  pose=BP1-8b6f28a6ef2ed0ee (rest, classic31_v1) [not a library pose]
  evidence=sim(open-loop commanded) min_conf=0.6 ok=24
  unknown=none
  fault=head.jaw:weak,rightArm.shoulder:stall
  absent=head.rollNeck,torso.midStom
  off=none
  estop=released motion_allowed=true
  verified_age=0.0s (oldest joint) last_write_age=0.0s
  ```
  Jaw alone gave `scope=joint`, `scope_limbs=[]` (re-checked after OPEN-16); adding `torso.topStom:brownout` gave `scope=body`.
- **NOVA may.** Summarise, retrieve `Ax-BOD-FAULT` poses, propose plans avoiding the channel (any goal is refused while a fault stands), ask about inspection.
- **Deterministic action.** `DEENERGIZE_TO_HARD_STOPS` at the given scope; `pose_id` re-minted (R4). `Tx-BOD-MISMATCH` and plan abort belong to the unbuilt executor.
- **Escalation.** Operator ack before a new plan. Body scope or more than one gravity joint → trainer, `S-LIMP` / SW1. A rehome keeps faults; nothing sets `faults.cleared_at` yet.

## 3. `goal_requested`

- **Detection.** `detect(goal={"pose_id": …, "requested_by": …})`; `requested_by` is accepted but not stored.
- **Goal checks** (`goal_checks()`; each failure is appended to `failed_checks`):
  1. the ID starts `BP1-` and is in `pose_library` (either failure returns at once);
  2. the library `pose_id` and its `checksums` recompute (§2.8);
  3. e-stop clear;
  4. `motion_allowed` true;
  5. no unknown joints and no rehome needed;
  6. no faulted joints in the current state;
  7. goal `calibration_id` and `body_profile` equal the current state's;
  8. every target (goal joint not `absent`) has a limit in `calibration_limits[calibration_id]` and its `q_deg` is inside it; no limit set or joint limit ⇒ refuse. `config.load_config()` now builds `calibration_limits` from the draft register `bod_store/calibration/register.json` (`register_version = draft-2026-10-13`, `signed = false`), with measured overrides from `calibration/measured.json` (empty) merged on top as a pure read. Only the operator step `python -m calibration.apply_overrides` writes: it appends every applied/rejected override to `calibration/overrides.log` with a fresh `event_id` and a CT stamp, on every run (no dedupe). Readers report `calibration_unapplied` (overrides not yet in the log) and the block adds `calibration: N unapplied overrides` when N > 0;
  9. no target is absent, missing or `off` in the current state (an `unknown` target already fails check 5);
  10. no target is stale (oldest target `verified_at`);
  11. the goal library record itself has no faulted (weak/stuck/stall/disconnected/dead/fault), `offline`, `unknown`, or unexpected `absent` joints (interim always-absent head.rollNeck / torso.midStom are allowed).
- **Trigger.** Refused: `high`, `REFUSE_MOVE`, `reason = "refused: <checks>"`. Valid: `info`, `PASS_TO_VALIDATOR`, `reason = "all goal checks passed; hand to deterministic validator"`. Both carry `goal_pose_id` and `failed_checks`.
- **Sim results** (re-run 2026-10-13 after OPEN-16, temp DB, after rehome to rest): E1 rest, E2 ready → pass. E3 → refused (null targets + `goal joint … offline` ×4). E4 → refused (null shoulder + `goal joint rightArm.shoulder faulted (weak)`). E5 → refused (`goal joint leftArm.bicep faulted (stuck)`). E6 → refused (`goal joint torso.topStom faulted (weak)`). E7 / E8 → refused here only because the current state is classic31 (calibration/profile differs).
- **Sim results with the draft register** (2026-10-13 ~20:18 CT, temporary DB):
  - E7 `fs_contact_stop` and E8 `ra_hold_object`, each on clean state (rehome to its own pose, `motion_allowed` true): **pass** (`failed_checks = []`).
  - Same, after `set_estop(True)`: **refused** (`estop active`, `motion_allowed false`).
  - A synthetic goal (E2 `ready` with `rightArm.omoplate` 95, re-minted, inserted only into the temp DB's library): **refused** (`rightArm.omoplate target 95 outside limits [10, 80]`). E2 unchanged passes as a control.
- **NOVA sees.** The current-state block (no goal lines).
- **NOVA may.** Propose a `MotionPlan` quoting only retrieved IDs, or ask for clarification.
- **Deterministic action.** `REFUSE_MOVE`, or hand to the validator. `PASS_TO_VALIDATOR` is not permission to move; the validator/executor (edges, §4.2 prunes, §8.3 verify) is not built.
- **Escalation.** Repeated refusals or out-of-library goals → operator. Hardware plans need human review until §9 phase 2.

## 4. `state_stale`

- **Detection.** Over the live (ok/fault) joints: any joint with `verified_at` NULL or older than `stale_after_s` (5.0 s, PLACEHOLDER) is listed; age is measured on the **oldest** verified joint. Also stale with no live joints or all unknown, and on an empty store. Open-loop sim writes never verify, so a freshly loaded example with no rehome reads `verified_age=never … STALE`. `last_write_at` / `write_age_s` are reported separately and do not affect staleness.
- **Trigger.** `medium`, `joints = <stale joints>`, `reason = "oldest verification age <a>s > <limit>s"`, or `"never verified"` when no live joint has a verify time (sim: freshly loaded E8), `HOLD`.
- **NOVA sees** (sim, 30 s after the last rehome):
  ```text
  [BOD body status | read-only | LLM may not command motion]
  pose=BP1-642ad843a4151a32 (rest, classic31_v1) [not a library pose]
  evidence=sim(open-loop commanded) min_conf=0.6 ok=23
  unknown=none
  fault=head.jaw:weak,rightArm.shoulder:stall,torso.topStom:brownout
  absent=head.rollNeck,torso.midStom
  off=none
  estop=released motion_allowed=true
  verified_age=30.0s (oldest joint) last_write_age=30.0s STALE(>5.0s) - do not trust pose
  ```
  NOVA never fills in a pose from memory.
- **NOVA may.** Say the state is unverified and ask for a refresh. No plans.
- **Deterministic action.** `HOLD`; goals fail check 10. S-FREEZE → S-REST-SLOW / S-LIMP sequencing is executor work (not built).
- **Escalation.** Long staleness → operator (timeout PLACEHOLDER). Disk/corruption → §6.2 S3 guard.

## 5. `power_cut_rehome_needed`

- **Detection.** `meta.rehome_required = 1`, set by `mark_unknown(None, reason="power_cut")`, cleared only by `rehome()`. The pose reads `unknown` with `library_match = false` (`[not a library pose]`). Detection from rails, SW1, boot or MRL restart is not built.
- **Trigger.** `high`, `joints = <all unknown>`, `reason = "power cut: joints unknown until re-homed (OPEN-W24)"`, `REQUEST_REHOME`. `joint_unknown/HOLD` and `state_stale/HOLD` (joints `[]`) fire with it; a goal is refused (`unknown joints/rehome`, `stale state`).
- **NOVA sees** (sim):
  ```text
  [BOD body status | read-only | LLM may not command motion]
  pose=BP1-155bbfbd34614cdc (unknown, classic31_v1) [not a library pose]
  evidence=none min_conf=0.0 ok=0
  unknown=head.eyeX,head.eyeY,head.jaw,head.neck,head.rothead,leftArm.bicep,+20
  fault=none
  absent=head.rollNeck,torso.midStom
  off=none
  estop=released motion_allowed=true
  verified_age=never (oldest joint) last_write_age=0.0s STALE(>5.0s) - do not trust pose
  REHOME REQUIRED (power cut): no motion except supervised homing.
  ```
- **NOVA may.** Explain, ask the operator to confirm the area is clear and the rest support is in place, propose homing.
- **Deterministic action.** `REQUEST_REHOME`. `rehome(pose_id)` is a **simulated** supervised homing to a library pose: it sets `verified_at`, keeps faults and `off` joints (re-mints the ID), never clears the e-stop latch, and with no faults or off joints restores the library `pose_id`.
- **Escalation.** Operator ack mandatory. Homing failure → `fault_raised`. Repeated cuts → trainer + hardware inspection.

## 5a. `estop_active`

- **Detection.** Latched `meta.estop = active`, raised by a record's `health.estop=active` or `SimStore.set_estop(True)`. **Simulated flag; no sensed SW1 input.**
- **Trigger.** `high`, `joints = []`, `reason = "E-stop SW1 active (R5)"`, `DEENERGIZE_TO_HARD_STOPS`, `scope = body`, `scope_limbs = []`. Any `fault_raised` in the same pass is widened to body; goals fail checks 3 and 4.
- **NOVA sees** (sim: e-stop raised, then rehome to rest; the latch survived):
  ```text
  [BOD body status | read-only | LLM may not command motion]
  pose=BP1-ddcab2230df85dcc (rest, classic31_v1)
  evidence=sim(open-loop commanded) min_conf=0.6 ok=26
  unknown=none
  fault=none
  absent=head.rollNeck,torso.midStom
  off=none
  estop=ACTIVE motion_allowed=false
  verified_age=0.0s (oldest joint) last_write_age=0.0s
  ```
- **NOVA may.** Explain. It cannot release the e-stop (no path from the adapter or triggers) and may not plan motion.
- **Deterministic action.** De-energise the whole body; `motion_allowed` held at false.
- **Release.** Operator only: `estop_release.release_estop(operator_id, reason)` writes an `estop_audit` row (sim: `2026-10-13 20:10:59 CDT`, operator `op1`, reason `sim test`). Empty id or reason is refused. Motion stays disabled until `motion_allowed` is set again. The physical SW1 remains the real authority.

## 6. `current_out_of_range` — DEFERRED (until current sensors exist)

Not in `triggers.py`; no store fields. Design intent kept: per-rail INA219/INA226 (B-44/B-45) with `rail_current_ma`, `rail_bus_v`, `i_limit_ma` (PLACEHOLDER), `stall_ms`, undervoltage limit. A sustained stall → `raise_fault(kind="stall")` (digit 2, limb scope unless a joint-scope channel). A rail overcurrent could use the store's `rail`/`power` kinds, which give body scope. Caveats: stock INA219 (±3.2 A) is below a 15 A rail; 6 rails need two I2C buses or a mux; current gives stall, not position (§5.3).

## 7. `balance_support_lost` — DEFERRED (until support/balance sensing exists)

Not in `triggers.py`. The store has only a `motion_allowed` flag (from the record or simulated), which blocks goals; no `tip_risk`, `com_class`, `stand_clamped` or `support_ok`. Design intent kept: `S-FREEZE` (no automatic `S-LIMP`), retract-only edges, speed cap, trainer alerted, SW1 the authority. Pole-mounted InMoov: tip-over, not gait (OPEN-W5/W6).

---

## 8. Fields still not in the store

Now covered (no longer pending): `db_path`, events table, `faults.cleared_at` column, per-joint `verified_at` and `last_write_at`, `stale_after_s`, `rehome_required`, `goal_pose_id`, `failed_checks`, `scope` / `scope_limbs`, latched `estop` with `estop_audit`, `motion_allowed`, intentional-off status with `switch_on`, `library_match`, block lines for off / estop / motion_allowed / verified and write age.

Still not held:
- **Store/record:** `writer_heartbeat_at`, per-joint `homed`, `power_epoch` / `boot_id`, `health.power_rails`, `health.modifiers[]`, `balance_profile` detail (`tip_risk`, `com_class`)
- **Events:** `sink_taken`, `ack_required`, `acked_by`, `acked_at`; events do not record which trigger fired
- **Goal request:** `requested_by` (accepted, not stored), `request_id`
- **NOVA block:** `events`, `allowed_actions`, `nova_block_version`
- **Current sensing / support:** DEFERRED with §6–§7

## 9. OPEN

**Closed:**
- ~~BP1- IDs not on the §2.8 rule~~ (`bod_ids.py`).
- ~~`verified_at` was a write time; staleness used the newest row~~.
- ~~Pose kept the old name after a power cut~~.
- ~~Goal check was only a `BP1-` prefix check~~.
- ~~Scope of `DEENERGIZE_TO_HARD_STOPS` unspecified~~.
- ~~Commanded offline counted as a fault~~.
- ~~No e-stop in the store~~ (sim flag; sensed input stays a blocker).
- ~~**OPEN-16** (sim run findings)~~ — CLOSED 2026-10-13 20:09:51 CT (127/127 tests, re-checked in sim):
  - ~~(a) a rehome cleared an active e-stop~~ → latched; only `release_estop(operator_id, reason)` clears it, with audit.
  - ~~(b) a gravity joint switched off fired no trigger~~ → becomes `unknown`, fires `joint_unknown`.
  - ~~(c) goals containing faulted joints passed~~ → goal check 11 (E5, E6 refused).
  - ~~(d) `"age Nones"` in the stale reason~~ → `"never verified"`.
  - Also: `scope_limbs` is `[]` for joint scope.
- ~~Pending design question: does rehoming keep intentionally-off joints off?~~ — CLOSED: yes; only `switch_on()` restores, as `unknown` until rehomed.

- ~~Frozen calibration for E7/E8~~ — **DRAFTED (sim, unverified)** 2026-10-13: `bod_store/calibration/register.json` covers classic31 (26 joints), right_arm_v1 (10) and finger_starter_v1 (1). Every value is an MRL default (`kind = mrl_default`), cited to a pinned commit/line, and `verified: false`; `measured.json` is empty. E7 and E8 now pass on clean state and are refused under e-stop. **Measurements from the target build are still needed before hardware**, and the register is unsigned. head.rollNeck and torso.midStom stay absent with no limits (OPEN-W9); torso.lowStom is excluded (OPEN-W10).

**Calibration flags (from the register):**
- **eyeX/eyeY (OPEN-W9).** `InMoov2FullStart.py` maps both eyes 0–180 → output 60–120 (e.g. L236 `i01_head_eyeX.map(0.0,180.0,60.0,120.0)`), which conflicts with other sources on the output range. Input limits 0–180 are used as-is; rest 90.
- **Finger/wrist rest: start script vs Java.** `InMoov2FullStart.py` sets finger rest = 2 and wrist rest = 90; MRL `InMoov2HandConfig.java` has rest = 0 for all of them (e.g. wrist L120 `wrist.rest = 0.0`). The register uses the start-script values for both hands. This matters for homing targets; resolve against the real build.

**Sim-MVP blockers:**
1. **Sensed e-stop input.** The e-stop is a simulated flag; SW1 has no sensed input.
2. **Operator auth + structurally separate release.** `release_estop()` accepts any non-empty `operator_id` string, and it is kept away from the adapter/triggers only by an AST test, not by a separate process or permission boundary.
3. **Power-fault kinds.** Confirm `power`, `power_fault`, `brownout`, `rail`, `rail_off` (they force body scope).
4. **OPEN-W13.** Argument order of `moveArm`/`moveHand`/`moveHead`/`moveTorso` must be verified against the InMoov2 Java source before the library generator relies on it.

**Still OPEN:**
5. Placeholders: `stale_after_s` = 5.0 s (single limit), events retention (`events_retention_days = null`, `purge_events()` no-op), simulated re-homing, escalation timeouts.
6. Ack workflow (`ack_required`/`acked_*`) and `sink_taken` records.
7. Homing procedure, speed and verify evidence level (`evidence_level`, §10.1).
8. MRLComm link watchdog and serial watchdog (§10.1 §3.7); no heartbeat field.
9. Current sensing (§6, DEFERRED).
10. Stand/support model (§7, DEFERRED; OPEN-W5/W6).
11. S-FREEZE time limit and S-LIMP vs S-FREEZE policy owner (OPEN-W14, OPEN-W21).
12. Promotion of events / `Tx-BOD-MISMATCH` to NOVA after ack; the NOVA.db write hold stands.
13. BT1-/BPL1- prefixes pending maintainer approval (OPEN-W23); none minted.
14. OPEN-W24: arms-down hard-stop rest as the hardware default.
15. The deterministic validator/executor is not started; the safe actions are not enforced on hardware.
16. Calibration register values must be replaced by measurements from the target build (via `measured.json`) and signed before hardware.
17. ~~**OPEN-17:** `load_config()` could append to `calibration/overrides.log`, so the read-only adapter path wrote a file once `measured.json` had entries.~~ — **CLOSED 2026-10-13 (~20:25 CT), verified independently on a clean copy:** with 2 measured overrides, `load_config`, `body_status`, `body_status_block`, `body_status_json` and `detect` (incl. a goal) made no Python write-mode `open()` (audit hook) and changed no file under the copy (hash + mtime); the block showed `calibration: 2 unapplied overrides`. Two runs of `apply_overrides` logged 4 rows with 4 distinct `event_id`s (repeat apply re-logged both); unapplied then read 0. 145/145 tests pass; `tests/test_open17.py` covers it.
18. **OPEN-18 (minor, from the OPEN-17 check): PARTLY VERIFIED independently 2026-10-13 ~20:30 CT on a clean copy (153/153 tests). (a), (b) and (c) all CLOSED.** ~~(a) live-file reads / partial-corruption gap~~ — **CLOSED 2026-10-13 ~20:45 CT** after independent review's second independent check (166/166 tests).
    - **(a) CLOSED — independent verification of the partial-corruption fix, 2026-10-13 ~20:45 CT** (clean copy, an independent `LD_PRELOAD` shim; 166/166 tests; `bod_validate --negatives` 8/8 + 6/6; real `bod_store/` hashes and NOVA.db unchanged):
      - **Corruption sweep.** The snapshot was grown to 46 pages so `faults`, `events`, `pose_library` and `estop_audit` have interior + non-root leaf pages. Then one random 4 KB page was written in turn over every page: 10 b-tree roots (tables + autoindexes), 20 non-root leaves (faults 3, events 13, pose_library 2, estop_audit 2), 15 overflow pages and page 1.
      - **46/46 pass.** Each case gives `snapshot_corrupt` (quick_check); `detect(goal)` → `state_stale/HOLD` + `goal_requested/REFUSE_MOVE`; `failed_checks=["snapshot_corrupt"]`; `motion_allowed=false`; JSON `estop: null`; block `estop=UNKNOWN`. No exception from `body_status*`/`detect`/`goal_checks`, no write/create-flag open, no open of `bod_state.db`/`-wal`/`-shm`, and hashes + listing unchanged. The low-level `connect_ro()` / `snapshot()` raise a typed `SnapshotError`, which is their documented contract.
      - **Six earlier fail-closed cases** (missing, chmod 000, random bytes, zeroed pages, truncated, no path): all `estop: null` / `estop=UNKNOWN`, `HOLD` + `REFUSE_MOVE`, zero writes, no live opens.
      - **OK snapshot keeps the latch:** `estop` false → true after `set_estop(True)` → still true after a rehome → false after `release_estop` (with `motion_allowed` staying false).
      - **Cosmetic:** a multi-line quick_check message spills the `NO SNAPSHOT …` text onto a second block line (e.g. `Tree 3 page 3: btreeInitPage() …`). The content is still correct and `estop=UNKNOWN` is present.
    - **(a) partial-corruption fix (design review, 2026-10-13 20:37:04 CDT):** NOVA's snapshot open now runs `PRAGMA quick_check` on the `mode=ro&immutable=1` connection (O_RDONLY, confirmed by the trace shim); any result other than `ok` (or quick_check raising) => `snapshot_corrupt`. Defense in depth: `body_status*`, `detect` and `goal_checks` (pose-library/current_state/meta lookups) map any `sqlite3.Error` mid-read to `snapshot_corrupt` fail-closed output (`state_stale/HOLD`, goal `REFUSE_MOVE` with `snapshot_corrupt` in `failed_checks`, `motion_allowed=false`) and never raise. On any `snapshot_*` fail-closed, `estop` is `null` in JSON and the block says `estop=UNKNOWN` (independent review's minor). Latch semantics for ok snapshots unchanged. Tests: single-page corruption of `pose_library`'s root page and a sweep of every table's root page under the trace shim, plus monkeypatched mid-read `DatabaseError`/`OperationalError` cases.
    - **(a) independent re-verification, 2026-10-13 ~20:40 CT** (clean copy, an independent `LD_PRELOAD` shim covering open/open64/openat/openat64/fopen, independent of `tests/trace/`; 159/159 tests; `bod_validate --negatives` 8/8 + 6/6):
      - **Live writer:** writer held `BEGIN IMMEDIATE` with an uncommitted insert, on top of an 8,272-byte `-wal`. Across all 6 NOVA entry points (`body_status`, block, JSON, `connect_ro`, `snapshot`, `detect` + `goal_checks` with a goal), the only DB opens were `nova_snapshot.db` with flags `0xa0000` (O_RDONLY): 11 opens, 0 write/create flags, 0 opens of `bod_state.db`, `-wal` or `-shm`. Hashes, mtimes and the directory listing were unchanged. Positive control: the old `mode=ro` live read is caught by the same shim (`-wal`/`-shm` `0xa0042`).
      - **Fail closed** (missing, chmod 000, random bytes, zeroed pages, truncated, `nova_snapshot_path: null`): each gives `snapshot_*` status, `state_stale/HOLD`, `REFUSE_MOVE` with `failed_checks=[<status>]`, `motion_allowed=false`, a `NO SNAPSHOT …` block, zero writes, and no live-db open.
      - **Refresh:** a commit (`switch_off`), `set_estop`, and `release_estop` each refreshed the snapshot, and NOVA saw the new pose, e-stop and audit row. The snapshot header is bytes 18/19 = 1/1 with `journal_mode=delete` (not WAL), and there are no side files.
      - **Atomic swap:** a swap forced between NOVA's connect and backup returned the consistent pre-swap state (`motion_allowed=true`) and the next read showed the new one. In a stress run, 1,155 reads during 300 swaps gave 0 errors.
      - **Age line:** `oldest_joint_age=0.0s snapshot_age=60.0s … STALE(>5.0s, driven by snapshot)` with fresh joints. **REJECTED message:** `calibration: REJECTED (duplicate key 'leftArm.bicep' (measured.json)); overrides ignored` names the file once; apply gives `REFUSED`, exit 2.
      - **Gap (keeps (a) OPEN):** the corrupt check only reads `current_state` and `meta`. With one random page written into `pose_library`'s root page, `snapshot_status` stays `ok`, the block renders, `detect()` without a goal works, but `detect(goal=…)` **raises `sqlite3.DatabaseError: database disk image is malformed`** instead of returning `REFUSE_MOVE`. No motion results, but the trigger path crashes rather than failing closed. Fix: run `PRAGMA quick_check` (or catch `sqlite3.DatabaseError` in `goal_checks`/`detect`) and map it to `snapshot_corrupt`.
      - **Minor:** in the fail-closed JSON, `estop` reads `false` (the default), which can be misread as "released"; `null`/unknown would be safer.
    - Design review report: NOVA now reads only the operator snapshot, opened with `mode=ro&immutable=1`, and never the live db. Missing, unreadable or corrupt snapshot fails closed (`state_stale/HOLD`, `REFUSE_MOVE`, `motion_allowed=false`). The block labels `oldest_joint_age=` and `snapshot_age=` separately and says which one drove staleness. `tests/test_open18a.py` uses an `LD_PRELOAD` open shim while a writer holds an open transaction on a live `-wal`. In all 6 entry points it found no write-flag opens, no opens of the live db/`-wal`/`-shm`, identical hashes and an identical directory listing. Fail-closed cases are tested too, plus a negative control showing the shim catches the old `-shm` O_RDWR open. Original finding: checked with an `LD_PRELOAD` open() shim (catches SQLite's C-level opens) plus hash/mtime of every file:
      - No `-wal`/`-shm`, and the operator snapshot path: no side files created, no bytes changed. **But** every read opens the DB/snapshot `O_RDWR|O_CREAT`, because the URI is `immutable=1` without `mode=ro`. `mode=ro&immutable=1` opens `O_RDONLY` (checked).
      - Live `-wal` (writer connection open): the reader opens `-wal` and `-shm` `O_RDWR|O_CREAT`, and **`bod_state.db-shm` bytes change** on each read. The main DB is opened read-only. So NOVA reads of the live file are not zero-write while a writer is active; only the operator snapshot is.
      - `snapshot_age=` shows in the block. A snapshot aged 60 s (mtime set back) with joints verified 0.2 s earlier in the live DB fires `state_stale/HOLD` and refuses goals, as designed. With the snapshot aged, the `verified_age=… (oldest joint)` label shows the snapshot age (60 s), not the joints' age.
    - ~~(b) `apply_overrides.py` fails as a script~~ CLOSED: `python calibration/apply_overrides.py` works from `bod_store/` and from another directory. `python -m calibration.apply_overrides` works from `bod_store/`; from another directory it needs `PYTHONPATH=<bod_store>` (normal for `-m`). 4 applies → 4 distinct `event_id`s.
    - ~~(c) duplicate key silently collapsed~~ CLOSED: reader block shows `calibration: measured.json REJECTED (duplicate key 'leftArm.bicep' …); overrides ignored`, limits stay at register values, zero writes. Apply prints `REFUSED: …; nothing logged`, exit 2, `overrides.log` hash unchanged (from both directories).
    - Fix as described: Fix: (a) snapshot reads, see NOVA access; `tests/test_open18.py` checks that every adapter/trigger entry point leaves the directory listing and file bytes identical with `-wal`/`-shm` absent. (b) `apply_overrides.py` puts its install dir on `sys.path` from `__file__`, so both `python calibration/apply_overrides.py` and `python -m calibration.apply_overrides` work, from any cwd. (c) `measured.json` is parsed with an `object_pairs_hook` that rejects duplicate keys: the reader ignores overrides and flags `calibration: REJECTED (duplicate key … (measured.json))` (file named once) (`measured_error`) without writing anything; apply prints `REFUSED`, logs nothing and exits 2. Original findings:
    - (a) The read-only SQLite connection (`mode=ro` on a WAL database) makes SQLite itself create `bod_state.db-wal` (0 bytes) and `bod_state.db-shm` (32 KB) if absent; the DB file is unchanged. These are C-level opens, not visible to a Python hook, and pre-date OPEN-17. Options: `immutable=1` / a snapshot copy for NOVA, or accept (§0 read-only path note).
    - (b) `python calibration/apply_overrides.py` (as written in its docstring and `__main__`) fails with `ModuleNotFoundError: calibration`; it works as `python -m calibration.apply_overrides` from `bod_store/`.
    - (c) `measured.json` is a JSON object, so a duplicate joint key is silently collapsed by the parser (last one wins) and never reaches the log; "duplicates" can only be repeat applies.
