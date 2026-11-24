-- Inputs and outputs behind each automated monthly allocation publication
-- (capital history, idle deductions, pins, readiness, yields, preview and the
-- independent verification). Append-only and content-addressed; written in
-- the same transaction as the allocation_monthly_results revision they back.
CREATE TABLE IF NOT EXISTS allocation_monthly_artifacts (
    revision_id TEXT NOT NULL REFERENCES allocation_monthly_results(revision_id),
    name TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    prime TEXT NOT NULL,
    month DATE NOT NULL CHECK (EXTRACT(DAY FROM month) = 1),
    code_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content BYTEA NOT NULL,
    PRIMARY KEY (revision_id, name)
);
CREATE INDEX IF NOT EXISTS allocation_monthly_artifacts_month
    ON allocation_monthly_artifacts (prime, month);
