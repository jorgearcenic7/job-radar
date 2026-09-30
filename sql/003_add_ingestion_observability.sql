CREATE TABLE IF NOT EXISTS ingestion_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'success', 'partial', 'failed')),
    sources_total INTEGER NOT NULL DEFAULT 0 CHECK (sources_total >= 0),
    sources_succeeded INTEGER NOT NULL DEFAULT 0
        CHECK (sources_succeeded >= 0),
    sources_failed INTEGER NOT NULL DEFAULT 0 CHECK (sources_failed >= 0),
    jobs_seen INTEGER NOT NULL DEFAULT 0 CHECK (jobs_seen >= 0),
    jobs_new INTEGER NOT NULL DEFAULT 0 CHECK (jobs_new >= 0),
    jobs_closed INTEGER NOT NULL DEFAULT 0 CHECK (jobs_closed >= 0),
    matches INTEGER NOT NULL DEFAULT 0 CHECK (matches >= 0)
);

CREATE TABLE IF NOT EXISTS source_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingestion_run_id BIGINT NOT NULL
        REFERENCES ingestion_runs (id) ON DELETE CASCADE,
    company TEXT NOT NULL,
    source TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'success', 'failed')),
    jobs_seen INTEGER NOT NULL DEFAULT 0 CHECK (jobs_seen >= 0),
    jobs_new INTEGER NOT NULL DEFAULT 0 CHECK (jobs_new >= 0),
    jobs_closed INTEGER NOT NULL DEFAULT 0 CHECK (jobs_closed >= 0),
    matches INTEGER NOT NULL DEFAULT 0 CHECK (matches >= 0),
    duration_ms BIGINT CHECK (duration_ms >= 0),
    error_type TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_started_at
ON ingestion_runs (started_at DESC);

CREATE INDEX IF NOT EXISTS idx_source_runs_ingestion_run_id
ON source_runs (ingestion_run_id);

CREATE INDEX IF NOT EXISTS idx_source_runs_source_started_at
ON source_runs (source, company, started_at DESC);
