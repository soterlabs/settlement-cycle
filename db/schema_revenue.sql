-- Immutable daily estimates. Successful publication is a committed row; failed
-- attempts belong to the worker ledger and cannot replace an existing result.
CREATE TABLE IF NOT EXISTS revenue_results (
    revision_id TEXT PRIMARY KEY,
    publication_order BIGSERIAL NOT NULL UNIQUE,
    prime TEXT NOT NULL,
    cutoff DATE NOT NULL,
    opening_pins JSONB NOT NULL,
    closing_pins JSONB NOT NULL,
    code_version TEXT NOT NULL,
    configuration_version TEXT NOT NULL,
    input_revision TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    result JSONB NOT NULL,
    result_hash TEXT NOT NULL,
    provisional BOOLEAN NOT NULL DEFAULT TRUE CHECK (provisional),
    excluded_inputs JSONB NOT NULL DEFAULT '["monthly_distribution_rewards"]'::jsonb
);
CREATE INDEX IF NOT EXISTS revenue_results_latest
    ON revenue_results (prime, cutoff DESC, publication_order DESC);

CREATE TABLE IF NOT EXISTS revenue_attempts (
    attempt_id UUID PRIMARY KEY,
    prime TEXT NOT NULL,
    cutoff DATE NOT NULL,
    versions JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','succeeded','failed','abandoned')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    error_type TEXT,
    revision_id TEXT REFERENCES revenue_results(revision_id)
);
CREATE INDEX IF NOT EXISTS revenue_attempts_latest ON revenue_attempts (prime, started_at DESC);
