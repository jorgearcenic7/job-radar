CREATE TABLE job_feedback (
    source TEXT NOT NULL,
    source_job_id TEXT NOT NULL,
    match_rules_version INTEGER NOT NULL
        CHECK (match_rules_version > 0),
    relevance TEXT NOT NULL
        CHECK (relevance IN ('relevant', 'not_relevant')),
    match_status TEXT NOT NULL,
    match_reason_codes TEXT[] NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, source_job_id, match_rules_version),
    FOREIGN KEY (source, source_job_id)
        REFERENCES jobs (source, source_job_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
);
