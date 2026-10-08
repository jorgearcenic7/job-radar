ALTER TABLE jobs
ADD COLUMN match_rules_version INTEGER;

ALTER TABLE jobs
ADD COLUMN match_reason_codes TEXT[];
