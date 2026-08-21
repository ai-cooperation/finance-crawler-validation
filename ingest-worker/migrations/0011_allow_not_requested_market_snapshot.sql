-- 0011: preserve the explicit no-market-data boundary.
--
-- `openbb_alignment_cli --skip-market-data` emits provider=not_requested with
-- an empty instruments array. The original 0005 CHECK required at least one
-- instrument, which made a valid research requirement fail during the remote
-- market-alignment write. SQLite cannot alter a CHECK constraint in place.
-- Rebuild the parent and every table that references it so the foreign keys
-- point at the new parent after the migration. The application contract still
-- controls the meaning of provider=not_requested.
PRAGMA foreign_keys = OFF;

ALTER TABLE research_reports RENAME TO research_reports_legacy_0011;
ALTER TABLE tradingagents_plans RENAME TO tradingagents_plans_legacy_0011;
ALTER TABLE topic_market_alignments RENAME TO topic_market_alignments_legacy_0011;
ALTER TABLE market_snapshots RENAME TO market_snapshots_legacy_0011;

CREATE TABLE market_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  as_of TEXT NOT NULL,
  provider TEXT NOT NULL,
  object_key TEXT NOT NULL UNIQUE,
  content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
  instrument_count INTEGER NOT NULL CHECK (instrument_count >= 0),
  created_at TEXT NOT NULL
);

INSERT INTO market_snapshots (
  snapshot_id, run_id, as_of, provider, object_key,
  content_sha256, instrument_count, created_at
)
SELECT
  snapshot_id, run_id, as_of, provider, object_key,
  content_sha256, instrument_count, created_at
FROM market_snapshots_legacy_0011;

CREATE TABLE topic_market_alignments (
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

INSERT INTO topic_market_alignments (
  alignment_id, run_id, topic_snapshot_id, market_snapshot_id,
  generated_at, partial, coverage_ratio, object_key, content_sha256,
  topic_count, created_at
)
SELECT
  alignment_id, run_id, topic_snapshot_id, market_snapshot_id,
  generated_at, partial, coverage_ratio, object_key, content_sha256,
  topic_count, created_at
FROM topic_market_alignments_legacy_0011;

CREATE TABLE tradingagents_plans (
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

INSERT INTO tradingagents_plans (
  plan_id, run_id, topic_snapshot_id, alignment_id, decision, skip_reason,
  object_key, content_sha256, topic_count, created_at
)
SELECT
  plan_id, run_id, topic_snapshot_id, alignment_id, decision, skip_reason,
  object_key, content_sha256, topic_count, created_at
FROM tradingagents_plans_legacy_0011;

CREATE TABLE research_reports (
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
  report_profile TEXT NOT NULL DEFAULT 'detailed_traceable'
    CHECK (report_profile IN ('detailed_traceable', 'compact_traceable')),
  generated_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  evidence_count INTEGER NOT NULL CHECK (evidence_count >= 1),
  created_at TEXT NOT NULL
);

INSERT INTO research_reports (
  report_id, run_id, topic_snapshot_id, plan_id, alignment_id,
  market_snapshot_id, topic_id, object_key, content_sha256, model,
  agent_version, report_profile, generated_at, expires_at, evidence_count, created_at
)
SELECT
  report_id, run_id, topic_snapshot_id, plan_id, alignment_id,
  market_snapshot_id, topic_id, object_key, content_sha256, model,
  agent_version, report_profile, generated_at, expires_at, evidence_count, created_at
FROM research_reports_legacy_0011;

DROP TABLE research_reports_legacy_0011;
DROP TABLE tradingagents_plans_legacy_0011;
DROP TABLE topic_market_alignments_legacy_0011;
DROP TABLE market_snapshots_legacy_0011;

CREATE INDEX IF NOT EXISTS idx_market_snapshots_run
  ON market_snapshots(run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_topic_market_alignments_topic
  ON topic_market_alignments(topic_snapshot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tradingagents_plans_topic
  ON tradingagents_plans(topic_snapshot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_reports_topic
  ON research_reports(topic_id, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_reports_plan
  ON research_reports(plan_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_reports_profile
  ON research_reports(report_profile, generated_at DESC);

PRAGMA foreign_keys = ON;
