CREATE TABLE IF NOT EXISTS tradingagents_plans (
  plan_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  topic_snapshot_id TEXT NOT NULL REFERENCES topic_snapshots(snapshot_id),
  alignment_id TEXT NOT NULL REFERENCES topic_market_alignments(alignment_id),
  decision TEXT NOT NULL CHECK (decision IN ('eligible', 'skipped')),
  skip_reason TEXT NOT NULL,
  object_key TEXT NOT NULL UNIQUE,
  content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
  topic_count INTEGER NOT NULL CHECK (topic_count BETWEEN 0 AND 3),
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tradingagents_plans_topic
  ON tradingagents_plans(topic_snapshot_id, created_at DESC);
