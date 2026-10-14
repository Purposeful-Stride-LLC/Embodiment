# BOD trigger fields (triggers.py) — for doc matching

Updated 2026-10-13 20:10 CDT (America/Chicago): closes OPEN-16 (a–d) + intentional-off rehome latch + operator e-stop release.

Each trigger is a dict with these fields:

| Field | Type | Meaning |
|---|---|---|
| `trigger_event` | str | one of `estop_active` (NEW), `joint_unknown`, `fault_raised`, `goal_requested`, `state_stale`, `power_cut_rehome_needed` |
| `severity` | str | `info` / `medium` / `high` |
| `joints` | list[str] | affected channels (MRL names). `state_stale` now lists the stale joints |
| `reason` | str | short deterministic explanation (`state_stale` uses `"never verified"` when `age_s` is null) |
| `safe_action` | str | `HOLD`, `DEENERGIZE_TO_HARD_STOPS`, `REFUSE_MOVE`, `REQUEST_REHOME`, or `PASS_TO_VALIDATOR` (goal_requested only, when every goal check passes) |
| `safe_action_overrides_llm` | bool | always `true` |
| `pose_id` | str\|null | current `BP1-` pose id |
| `nova_context_block` | str | the `body_status_block()` text |
| `goal_pose_id` | str | goal_requested only |
| `failed_checks` | list[str] | NEW, goal_requested only: every failed check; empty ⇒ PASS_TO_VALIDATOR |
| `scope` | str | NEW, `fault_raised` and `estop_active`: `joint` / `limb` / `body` (scope of DEENERGIZE_TO_HARD_STOPS) |
| `scope_limbs` | list[str] | NEW, limbs to de-energise (`[]` when scope is `body` **or** `joint`) |

| trigger_event | default safe_action |
|---|---|
| estop_active | DEENERGIZE_TO_HARD_STOPS, scope=body |
| joint_unknown | HOLD |
| fault_raised | DEENERGIZE_TO_HARD_STOPS, scope by rule below |
| goal_requested | REFUSE_MOVE unless all goal checks pass, then PASS_TO_VALIDATOR |
| state_stale | HOLD |
| power_cut_rehome_needed | REQUEST_REHOME |

## Goal checks (`triggers.goal_checks`), all must pass
1. goal id starts `BP1-` and exists in `pose_library`;
2. library record's `pose_id` recomputes (§2.8) and its `checksums` (canonical_payload, joint_checksum, record_checksum) recompute;
3. `estop` not active; 4. `motion_allowed` true;
5. no unknown joints / rehome not required; 6. no faulted joints;
7. goal `calibration_id`/`body_profile` = current state's;
8. every target joint (goal joints with status ≠ absent) has a limit in `bod_config.json` `calibration_limits[calibration_id]` and its `q_deg` is inside it — a missing calibration set or joint limit ⇒ refuse;
9. no target joint is absent / missing from current_state / switched off / unknown;
10. no target joint is stale (oldest target `verified_at`);
11. **NEW (OPEN-16c):** the goal library record itself must not encode faulted (`weak`/`stuck`/`…`), `offline`, `unknown`, or unexpected `absent` joints (interim always-absent `head.rollNeck` / `torso.midStom` are allowed). E5 and E6 are refused by this check.

## DEENERGIZE_TO_HARD_STOPS scope rule
- **body**: E-stop active, or any power fault (`kind` ∈ power, power_fault, brownout, rail, rail_off).
- **joint**: only if every faulted joint is not gravity-loaded **and** carries no dependent joints: hand fingers, hand wrist (rotation), `head.jaw`, `head.eyeX`, `head.eyeY`. `scope_limbs = []`.
- **limb** (default): everything else, including non-gravity joints that carry others (`*Arm.rotate`, `head.rothead`), because cutting one joint on a gravity-loaded limb can drop the rest of the limb onto it. `scope_limbs` lists every affected limb.
- Several faults: the widest scope wins.

## E-stop latch (OPEN-16a) and operator release
- `meta.estop` is **latched**. `update_from_sim` / `rehome` may raise it from a record's `health.estop=active`, but **never** clear it when the record says `released`.
- `SimStore.set_estop(True)` raises the latch and forces `motion_allowed=0`. `set_estop(False)` is refused.
- `motion_allowed` cannot become true while e-stop is active (`set_motion_allowed` and record loads).
- **Only** `estop_release.release_estop(operator_id, reason)` clears the latch. It requires a non-empty operator id and reason, appends an append-only `estop_audit` row (`ts`, `when_cdt` America/Chicago, `operator_id`, `reason`), and logs `estop_released` in `events`. Wall-clock timestamps (not the sim clock).
- `rag_adapter.py` and `triggers.py` must not import `estop_release` or reference `release_estop` / `set_estop` (enforced by AST test). The adapter's RO connection cannot write `meta.estop`.

- **Fail-closed e-stop reporting (OPEN-18a-2):** when NOVA fails closed for any `snapshot_*` reason (missing / unreadable / corrupt), `estop` is reported as **unknown**: JSON `"estop": null` and the block shows `estop=UNKNOWN`. Consumers must treat `null` as "not known released" (never as `false`). For `snapshot_status=ok`, `estop` stays a boolean read from the latched `meta.estop` exactly as above.
- **Snapshot integrity:** each NOVA snapshot open runs `PRAGMA quick_check` (read-only, immutable connection); non-`ok` => `snapshot_corrupt`. Any `sqlite3.Error` during a NOVA read (`body_status*`, `detect`, goal/pose-library lookups) also maps to `snapshot_corrupt` (`state_stale/HOLD`, goals `REFUSE_MOVE` with `snapshot_corrupt` in `failed_checks`, `motion_allowed=false`); these entry points never raise on DB errors.

## Intentional off / switch_on
- Non-gravity `switch_off(joint)` → `status='off'` (digit 5); not a fault.
- Gravity `switch_off(joint)` → `status='unknown'` (digit 4) immediately; `detect()` fires `joint_unknown` (OPEN-16b).
- `rehome` keeps `status='off'` joints off (and keeps faults). Only `switch_on(joint)` brings an off joint back; it returns as `unknown` until a subsequent rehome.
- Event log `ts` always uses the real wall clock at write time (`time.time()`), never the sim clock.

## Status JSON (`body_status_json`) keys
`pose_id, pose_name, body_profile, unknown, fault, absent, off (NEW), ok_count, min_conf, evidence, age_s, stale_after_s, stale, stale_joints (NEW), oldest_verified_at (NEW), last_write_at (NEW), write_age_s (NEW), rehome_required, library_match (NEW), estop (NEW), motion_allowed (NEW)`.
- `age_s` = now − **oldest** `verified_at` among the joints that matter (live ok/fault, or the goal targets); `null` if any was never verified.
- `pose_name` = `unknown` and `library_match` = false when no joint is ok/fault/off (power cut or all unknown).

## Store fields (`SCHEMA.sql` `current_state`)
- `status` vocabulary: `ok | unknown | absent | fault | off` (`off`: switched off on purpose, digit 5; no `faults` row, no fault_raised).
- `verified_at` (CHANGED): nullable; set **only** by a measured value (`sensed_deg` present → `source='measured'`) or a rehome. Open-loop sim writes keep the last rehome time.
- `last_write_at` (NEW): time of any write to the row.
- `meta` keys: `estop` (`active|released`, latched), `motion_allowed` (`1|0`).
- `estop_audit` table (NEW, append-only): operator release audit.
- SimStore methods: `switch_off(joint)`, `switch_on(joint)`, `set_estop(True)` (raise only), `set_motion_allowed(bool)`, `update_from_sim(..., verified=False)`, `rehome`. `raise_fault(kind="offline")` routes to `switch_off`. Operator release: `estop_release.release_estop`.
- Source vocabulary: `commanded | measured | sim`. No `BT1-`/`BPL1-` ids are minted (pending approval).

## OPEN-18 status fields (FIXED 2026-10-13, pending independent verification)
| field | type | meaning |
|---|---|---|
| `snapshot_taken_at` | float epoch s/null | mtime of the operator snapshot file (`nova_snapshot_path`, the only file NOVA opens, `mode=ro&immutable=1`) |
| `snapshot_age_s` | float | `now - snapshot_taken_at`; shown in the block as `snapshot_age=` |
| `snapshot_stale` | bool | snapshot age > `stale_after_s`; forces `stale` / `state_stale` (reason adds `snapshot age ...`) |
| `oldest_verified_at` | float/null | now the OLDER of snapshot time and `oldest_joint_verified_at` |
| `snapshot_status` | str | OPEN-18(a), FIXED pending independent verification: `ok` / `snapshot_missing` / `snapshot_unreadable` / `snapshot_corrupt`. Anything except `ok` ⇒ `stale`, `motion_allowed=false`, `state_stale/HOLD`, goals `REFUSE_MOVE` (`failed_checks=[status]`), block `NO SNAPSHOT …`. NOVA never opens the live db |
| `snapshot_error` | str | (only when not ok) the reason |
| `oldest_joint_age_s` | float/null | `now - oldest_joint_verified_at`; block `oldest_joint_age=` (was mislabelled `verified_age=… (oldest joint)`) |
| `stale_driver` | str/null | what made the state stale: `oldest_joint`, `snapshot`, `oldest_joint,snapshot`, `all_unknown`, `no_state` or a `snapshot_*` status; block `STALE(>Ns, driven by …)` |
| `measured_error` | str/null | measured.json rejected (e.g. duplicate key); overrides ignored, no write |

Apply step: `python calibration/apply_overrides.py` (works from any cwd) or `python -m calibration.apply_overrides` (run it from `bod_store/`, or set `PYTHONPATH=<bod_store>`); it exits 2 and logs nothing if measured.json has duplicate keys.
