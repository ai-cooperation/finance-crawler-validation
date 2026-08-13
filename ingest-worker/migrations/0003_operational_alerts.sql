CREATE TABLE IF NOT EXISTS operational_alerts (
  alert_key TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK (state IN ('open', 'resolved')),
  fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
  summary TEXT NOT NULL,
  first_detected_at TEXT NOT NULL,
  last_detected_at TEXT NOT NULL,
  last_notified_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_operational_alerts_state
  ON operational_alerts(state, last_detected_at DESC);

CREATE TABLE IF NOT EXISTS run_admissions (
  workflow_run_id TEXT PRIMARY KEY,
  commit_sha TEXT NOT NULL CHECK (length(commit_sha) = 40),
  decision TEXT NOT NULL CHECK (decision IN ('admitted', 'denied')),
  reason TEXT NOT NULL CHECK (reason IN ('admitted', 'daily_run_limit', 'minimum_interval')),
  requested_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_admissions_daily
  ON run_admissions(decision, requested_at DESC);
