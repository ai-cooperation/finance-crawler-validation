ALTER TABLE research_jobs ADD COLUMN requirement_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE research_jobs ADD COLUMN source_bundle_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE research_jobs ADD COLUMN planner_version TEXT NOT NULL DEFAULT 'unplanned';
ALTER TABLE research_jobs ADD COLUMN dispatch_id TEXT;

CREATE INDEX IF NOT EXISTS idx_research_jobs_planner
  ON research_jobs(planner_version, updated_at DESC);
