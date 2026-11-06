-- Monthly allocation analytics are separate from canonical settlements and
-- daily revenue. Immutable content-addressed revisions; retries are idempotent.
CREATE TABLE IF NOT EXISTS allocation_monthly_results (
    revision_id TEXT PRIMARY KEY,
    publication_order BIGSERIAL NOT NULL UNIQUE,
    prime TEXT NOT NULL,
    month DATE NOT NULL CHECK (EXTRACT(DAY FROM month) = 1),
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    document JSONB NOT NULL,
    provisional BOOLEAN NOT NULL DEFAULT TRUE CHECK (provisional)
);
CREATE INDEX IF NOT EXISTS allocation_monthly_results_latest
    ON allocation_monthly_results (prime, month, publication_order DESC);
