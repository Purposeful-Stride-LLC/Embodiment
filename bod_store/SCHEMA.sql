-- BOD embodiment-state store (bod.pose.v1). Separate from NOVA.db (spec §6.2 S1-S3).
-- Open-loop servos: angle_deg is the COMMANDED/estimated value unless source='measured'.
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- One row per joint (channel). pose_id is the current BP1- id (copied on every row).
CREATE TABLE IF NOT EXISTS current_state (
  joint       TEXT PRIMARY KEY,
  pose_id     TEXT NOT NULL CHECK (pose_id LIKE 'BP1-%'),
  pose_name   TEXT,
  body_profile TEXT NOT NULL,
  angle_deg   REAL,                       -- NULL when status in (unknown, absent)
  confidence  REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  status      TEXT NOT NULL CHECK (status IN ('ok','unknown','absent','fault','off')),  -- off = switched off on purpose (digit 5), not a fault
  detail      TEXT,                       -- original spec status (offline/weak/stuck/...)
  source      TEXT NOT NULL CHECK (source IN ('commanded','measured','sim')),
  energized   INTEGER NOT NULL DEFAULT 0,
  gravity     INTEGER NOT NULL DEFAULT 0,
  verified_at REAL,                       -- unix s; set ONLY by a measured value or a rehome (NULL = never verified)
  last_write_at REAL NOT NULL             -- unix s; any write to the row (sim/commanded/mark_unknown/fault/off)
);

CREATE TABLE IF NOT EXISTS faults (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  joint       TEXT NOT NULL,
  kind        TEXT NOT NULL,              -- offline/weak/stuck/stall/...
  detail      TEXT,
  raised_at   REAL NOT NULL,
  cleared_at  REAL
);

-- Append-only observation log. Retention: PLACEHOLDER (bod_config.json events_retention_days).
CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          REAL NOT NULL,
  kind        TEXT NOT NULL,
  joint       TEXT,
  pose_id     TEXT,
  payload     TEXT
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
WHEN (SELECT value FROM meta WHERE key='retention_purge') IS NOT '1'
BEGIN SELECT RAISE(ABORT, 'events is append-only (retention purge only)'); END;

CREATE TABLE IF NOT EXISTS pose_library (
  pose_id      TEXT PRIMARY KEY CHECK (pose_id LIKE 'BP1-%'),
  pose_name    TEXT NOT NULL,
  body_profile TEXT NOT NULL,
  schema_version TEXT NOT NULL CHECK (schema_version = 'bod.pose.v1'),
  nova_key     TEXT NOT NULL DEFAULT 'Ax-BOD-POSE',
  record_json  TEXT NOT NULL
);

-- Append-only e-stop release audit (estop_release.release_estop only).
CREATE TABLE IF NOT EXISTS estop_audit (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          REAL NOT NULL,
  when_cdt    TEXT NOT NULL,
  operator_id TEXT NOT NULL,
  reason      TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS estop_audit_no_update BEFORE UPDATE ON estop_audit
BEGIN SELECT RAISE(ABORT, 'estop_audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS estop_audit_no_delete BEFORE DELETE ON estop_audit
BEGIN SELECT RAISE(ABORT, 'estop_audit is append-only'); END;
