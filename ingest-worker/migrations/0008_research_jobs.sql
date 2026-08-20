CREATE TABLE IF NOT EXISTS research_jobs (
  job_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL UNIQUE,
  idempotency_key TEXT NOT NULL UNIQUE,
  subject TEXT NOT NULL,
  target_json TEXT NOT NULL,
  requirements_json TEXT NOT NULL,
  request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
  status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'blocked', 'completed', 'partial', 'failed', 'stale')),
  run_id TEXT,
  pack_id TEXT,
  report_count INTEGER NOT NULL DEFAULT 0 CHECK (report_count >= 0),
  error_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_research_jobs_status
  ON research_jobs(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS research_packs (
  pack_id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL UNIQUE REFERENCES research_jobs(job_id),
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  object_key TEXT NOT NULL UNIQUE,
  content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
  as_of TEXT NOT NULL,
  partial INTEGER NOT NULL CHECK (partial IN (0, 1)),
  stale INTEGER NOT NULL CHECK (stale IN (0, 1)),
  evidence_count INTEGER NOT NULL CHECK (evidence_count >= 0),
  report_count INTEGER NOT NULL CHECK (report_count >= 0),
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_research_packs_run
  ON research_packs(run_id, created_at DESC);
