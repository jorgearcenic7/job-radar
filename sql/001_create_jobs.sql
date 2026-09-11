CREATE TABLE IF NOT EXISTS jobs (
    source TEXT NOT NULL,
    source_job_id TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT,
    url TEXT NOT NULL,
    description TEXT,
    selected BOOLEAN NOT NULL DEFAULT FALSE,
    match_status TEXT,
    match_reason TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, source_job_id)
);
