-- Keep the large professional financial-depth payload out of the market
-- alignment request.  The snapshot/alignment contract remains compact while
-- the full depth object gets its own auditable R2 object and D1 index row.
CREATE TABLE IF NOT EXISTS financial_depths (
  market_snapshot_id TEXT PRIMARY KEY REFERENCES market_snapshots(snapshot_id),
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  object_key TEXT NOT NULL UNIQUE,
  content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
  status TEXT NOT NULL CHECK (status IN ('professional_ready', 'professional_partial', 'research_only', 'blocked')),
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_financial_depths_run
  ON financial_depths(run_id, created_at DESC);
