# Purposeful Stride — BOD embodiment-state store (proposed)

**Status: proposed design + simulation-only reference code. Not hardware-ready.**
Nothing here has been run on a physical robot. Do not connect it to servos.

## Purpose
BOD is a **proposed** embodiment-state store for a retrieval-augmented (RAG) agent built on
NOVA (a separate, proposed knowledge system that is not part of this repo). It keeps an
InMoov-class humanoid's self-state — quantized joint vector, per-servo fault mask, calibration
provenance, staleness, e-stop state — in its own local SQLite (WAL) store, and exposes a
**read-only** status view and decision triggers that an agent can cite. It runs with NOVA absent.

Contents:
- `BOD_EMBODIMENT_SPEC.md` — living spec (v0.3 draft); all concepts labelled proposed.
- `BOD_DECISION_TRIGGERS.md`, `bod_store/TRIGGER_FIELDS.md` — trigger/decision rules and fields.
- `bod_pose_state.schema.json` + `bod_pose_state.examples.json` — PoseState JSON Schema (draft 2020-12) and 8 examples.
- `bod_store/` — sim store, read-only RAG adapter, triggers, e-stop release, sim driver, tests.
- `bod_store/calibration/` — draft calibration register (MRL defaults, **unverified**), loader,
  `build_register.py`, `apply_overrides.py`; `measured.json` is empty (no bench measurements yet).
- `tools/bod_validate.py` — independent schema/checksum validator.

## Safety stance
- **The LLM never commands motion.** The agent only reads status and proposes goals; any motion
  would go through a deterministic validator/executor (not built) and an operator.
- **The e-stop is latched.** Once tripped it stays tripped until an operator explicitly releases it
  (`estop_release.release_estop(operator_id, reason)`), which is audited.
- **Fail-closed.** Missing, unreadable, corrupt or stale state ⇒ status `stale`, motion not allowed.
- Calibration values are unverified defaults; they must be replaced by measured, signed values before any hardware use.

## Quickstart
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cd bod_store && python init_db.py      # creates ./data/bod_state.db (git-ignored)
```

## Tests
```bash
cd bod_store && python -m pytest -q          # needs gcc for the open-trace shim test (Linux)
cd .. && python tools/bod_validate.py --negatives
```
`test_values_match_source_lines` re-reads the upstream MyRobotLab scripts and is **skipped**
unless `BOD_MRL_ROOT` (default `./upstream`) holds clones of `MyRobotLab/inmoov2` and
`MyRobotLab/inmoov` at the commits cited in `calibration/register.json`.
`build_register.py` uses the same `BOD_MRL_ROOT`.

## Credits
- **MyRobotLab** (Apache-2.0) — calibration defaults in `bod_store/calibration/register.json` are
  derived from MyRobotLab `inmoov2` / `inmoov` default scripts; each entry cites its source URL and
  line at a pinned commit. No MyRobotLab source files are included.
- **InMoov** by Gaël Langevin (inmoov.fr), licensed **CC BY-NC 3.0**. This project targets an
  InMoov-class robot. **No InMoov design files (STL, CAD, etc.) are included**, and nothing here
  relicenses the InMoov designs.

## License
Proprietary — all rights reserved by Purposeful Stride LLC. See `LICENSE`. In short: free
**30-day evaluation** (install and evaluate only); no redistribution, resale, sublicensing,
derivative or commercial use; continued use after 30 days needs written permission; no warranty.
Source being visible on GitHub does not grant any rights. Third-party credits above keep their own licenses.

## Reporting bugs
Please open a GitHub Issue on this repository with steps to reproduce, expected vs. actual
behaviour, and your OS/Python version. Feedback may be used by Purposeful Stride LLC (see `LICENSE` §4).
