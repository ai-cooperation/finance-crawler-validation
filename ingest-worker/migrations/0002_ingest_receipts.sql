CREATE TABLE IF NOT EXISTS ingest_receipts (
  run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
  payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
  status TEXT NOT NULL CHECK (status IN ('started', 'completed')),
  created_at TEXT NOT NULL,
  completed_at TEXT
);
