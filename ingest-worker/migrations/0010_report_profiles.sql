ALTER TABLE research_reports ADD COLUMN report_profile TEXT NOT NULL DEFAULT 'detailed_traceable'
  CHECK (report_profile IN ('detailed_traceable', 'compact_traceable'));

CREATE INDEX IF NOT EXISTS idx_research_reports_profile
  ON research_reports(report_profile, generated_at DESC);
