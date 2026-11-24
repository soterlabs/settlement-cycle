-- Manual replays of published daily revenue (for example after a venue or
-- configuration change), queued with scripts/request_revenue_replay.py and
-- executed on Railway by the nightly catch-up job, newest cutoff first.
-- next_cutoff records progress so an interrupted request resumes.
CREATE TABLE IF NOT EXISTS revenue_replay_requests (
    request_id BIGSERIAL PRIMARY KEY,
    prime TEXT NOT NULL,
    first_cutoff DATE NOT NULL,
    last_cutoff DATE NOT NULL CHECK (first_cutoff <= last_cutoff),
    reason TEXT NOT NULL CHECK (length(reason) > 0),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'done', 'failed', 'cancelled')),
    next_cutoff DATE,
    failed JSONB NOT NULL DEFAULT '[]'::jsonb,
    finished_at TIMESTAMPTZ
);
