ALTER TABLE source_runs
ADD COLUMN IF NOT EXISTS snapshot_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (snapshot_status IN ('unknown', 'healthy', 'suspicious', 'empty'));

ALTER TABLE source_runs
ADD COLUMN IF NOT EXISTS closure_suppressed BOOLEAN NOT NULL DEFAULT FALSE;
