CREATE TABLE IF NOT EXISTS research_reports (
  report_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  topic_snapshot_id TEXT NOT NULL REFERENCES topic_snapshots(snapshot_id),
  plan_id TEXT NOT NULL REFERENCES tradingagents_plans(plan_id),
  alignment_id TEXT NOT NULL REFERENCES topic_market_alignments(alignment_id),
  market_snapshot_id TEXT NOT NULL REFERENCES market_snapshots(snapshot_id),
  topic_id TEXT NOT NULL,
  object_key TEXT NOT NULL UNIQUE,
  content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
  model TEXT NOT NULL,
  agent_version TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  evidence_count INTEGER NOT NULL CHECK (evidence_count >= 1),
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_research_reports_topic
  ON research_reports(topic_id, generated_at DESC);

CREATE INDEX IF NOT EXISTS idx_research_reports_plan
  ON research_reports(plan_id, created_at DESC);
