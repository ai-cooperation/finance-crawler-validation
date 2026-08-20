CREATE TABLE IF NOT EXISTS market_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  as_of TEXT NOT NULL,
  provider TEXT NOT NULL,
  object_key TEXT NOT NULL UNIQUE,
  content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
  instrument_count INTEGER NOT NULL CHECK (instrument_count >= 1),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topic_market_alignments (
  alignment_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  topic_snapshot_id TEXT NOT NULL REFERENCES topic_snapshots(snapshot_id),
  market_snapshot_id TEXT NOT NULL REFERENCES market_snapshots(snapshot_id),
  generated_at TEXT NOT NULL,
  partial INTEGER NOT NULL CHECK (partial IN (0, 1)),
  coverage_ratio REAL NOT NULL CHECK (coverage_ratio >= 0 AND coverage_ratio <= 1),
  object_key TEXT NOT NULL UNIQUE,
  content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
  topic_count INTEGER NOT NULL CHECK (topic_count BETWEEN 0 AND 3),
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_snapshots_run
  ON market_snapshots(run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_topic_market_alignments_topic
  ON topic_market_alignments(topic_snapshot_id, generated_at DESC);
