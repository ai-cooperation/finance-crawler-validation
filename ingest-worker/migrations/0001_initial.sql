PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  workflow_run_id TEXT NOT NULL,
  commit_sha TEXT NOT NULL CHECK (length(commit_sha) = 40),
  snapshot_id TEXT NOT NULL UNIQUE,
  source_manifest_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('staging', 'published', 'failed')),
  collected_at TEXT NOT NULL,
  published_at TEXT,
  item_count INTEGER NOT NULL DEFAULT 0 CHECK (item_count >= 0)
);

CREATE TABLE IF NOT EXISTS raw_items (
  item_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  published_at TEXT,
  collected_at TEXT NOT NULL,
  transport TEXT NOT NULL,
  kind TEXT NOT NULL,
  layer TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  object_key TEXT NOT NULL UNIQUE,
  rights_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (source_id, canonical_url, content_sha256)
);

CREATE TABLE IF NOT EXISTS run_items (
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  item_id TEXT NOT NULL REFERENCES raw_items(item_id),
  PRIMARY KEY (run_id, item_id)
);

CREATE TABLE IF NOT EXISTS source_state (
  source_id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('success', 'partial', 'failed')),
  last_successful_crawl TEXT,
  last_article_date TEXT,
  cursor TEXT,
  last_snapshot_id TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topic_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  as_of TEXT NOT NULL,
  partial INTEGER NOT NULL CHECK (partial IN (0, 1)),
  failed_sources_json TEXT NOT NULL,
  object_key TEXT NOT NULL UNIQUE,
  content_sha256 TEXT NOT NULL,
  topic_count INTEGER NOT NULL CHECK (topic_count BETWEEN 0 AND 3),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS current_snapshot (
  singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
  snapshot_id TEXT NOT NULL REFERENCES topic_snapshots(snapshot_id),
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
  event_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  happened_at TEXT NOT NULL,
  payload_sha256 TEXT,
  previous_event_hash TEXT,
  event_hash TEXT NOT NULL UNIQUE,
  UNIQUE (run_id, stage, status, payload_sha256)
);

CREATE INDEX IF NOT EXISTS idx_raw_items_source_published
  ON raw_items(source_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_source_state_freshness
  ON source_state(last_successful_crawl);
