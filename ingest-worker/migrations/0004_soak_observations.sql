CREATE TABLE IF NOT EXISTS soak_observations (
  workflow_run_id TEXT NOT NULL,
  run_attempt INTEGER NOT NULL CHECK (run_attempt > 0),
  commit_sha TEXT NOT NULL CHECK (length(commit_sha) = 40),
  observed_at TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  PRIMARY KEY (workflow_run_id, run_attempt)
);

CREATE INDEX IF NOT EXISTS idx_soak_observations_observed_at
  ON soak_observations(observed_at DESC);
