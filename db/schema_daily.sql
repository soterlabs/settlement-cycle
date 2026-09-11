-- Daily pipeline + read API — tables owned by ``settle.store``.
--
-- Applied by ``settle.store.db.apply_schema`` on every cron run and at API
-- startup, so it must touch ONLY these tables: anything here that locks a
-- table shared with the monthly pipeline (as ``db/schema.sql``'s ALTER on
-- hypersync_logs does) would block that pipeline's readers nightly.

--
-- ``runs`` is the versioning spine: every fact row points at the run that
-- produced (or, for immutable on-chain facts, first saw) it. A run never
-- updates another run's rows; "latest" is the most recent status='ok' run
-- for (kind, prime, month). This is what turns a methodology restatement
-- into two visible runs instead of a silent rewrite of history.
CREATE TABLE IF NOT EXISTS runs (
    run_id          BIGSERIAL    PRIMARY KEY,
    kind            TEXT         NOT NULL,   -- 'tmf_history' | (phase 2) 'msc_mtd' | 'msc_settled' | …
    prime           TEXT,                    -- NULL for protocol-wide runs
    month           TEXT,                    -- 'YYYY-MM' or NULL
    pin_block       BIGINT,                  -- upper block bound of the run
    pin_ts          TIMESTAMPTZ,             -- its timestamp
    settle_version  TEXT         NOT NULL,   -- settle.__version__ / git sha
    config_hash     TEXT,                    -- sha256 of the config the run read
    started_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          TEXT         NOT NULL DEFAULT 'running',  -- running | ok | failed
    error           TEXT,
    summary         JSONB                    -- small run-level facts (row counts, totals, notes)
);
CREATE INDEX IF NOT EXISTS idx_runs_kind_status ON runs (kind, status, finished_at DESC);

-- Phase 1: Smart Burn Engine history. On-chain facts, immutable — keyed by
-- (block_number, log_index); re-runs are ON CONFLICT DO NOTHING.
CREATE TABLE IF NOT EXISTS sbe_kicks (
    block_number     BIGINT          NOT NULL,
    log_index        INTEGER         NOT NULL,
    block_time       BIGINT          NOT NULL,   -- unix seconds UTC
    tx_hash          TEXT            NOT NULL,
    usds_total       NUMERIC(38, 18) NOT NULL,   -- Kick.tot   (USDS pulled from the surplus)
    usds_buyback     NUMERIC(38, 18) NOT NULL,   -- Kick.lot   (→ Flapper)
    usds_to_stakers  NUMERIC(38, 18) NOT NULL,   -- Kick.pay   (→ USDS farm)
    sky_bought       NUMERIC(38, 18) NOT NULL,   -- Exec.bought
    splitter_burn    NUMERIC(20, 18) NOT NULL,   -- fraction in force
    splitter_hop     INTEGER         NOT NULL,
    farm             TEXT,
    flapper          TEXT,
    first_seen_run   BIGINT          REFERENCES runs (run_id),
    PRIMARY KEY (block_number, log_index)
);
CREATE INDEX IF NOT EXISTS idx_sbe_kicks_time ON sbe_kicks (block_time);

CREATE TABLE IF NOT EXISTS sky_burns (
    block_number     BIGINT          NOT NULL,
    log_index        INTEGER         NOT NULL,
    block_time       BIGINT          NOT NULL,
    tx_hash          TEXT            NOT NULL,
    sender           TEXT            NOT NULL,
    sink             TEXT            NOT NULL,   -- 0x…dEaD or the zero address
    sky_amount       NUMERIC(38, 18) NOT NULL,
    protocol         BOOLEAN         NOT NULL,   -- sender is the Pause Proxy
    first_seen_run   BIGINT          REFERENCES runs (run_id),
    PRIMARY KEY (block_number, log_index)
);
CREATE INDEX IF NOT EXISTS idx_sky_burns_time ON sky_burns (block_time);

CREATE TABLE IF NOT EXISTS sbe_param_changes (
    block_number     BIGINT   NOT NULL,
    log_index        INTEGER  NOT NULL,
    block_time       BIGINT   NOT NULL,
    tx_hash          TEXT     NOT NULL,
    contract         TEXT     NOT NULL,   -- chainlog role (MCD_SPLIT, MCD_KICK, MCD_FLAP …)
    address          TEXT     NOT NULL,   -- emitting contract
    what             TEXT     NOT NULL,
    value            TEXT     NOT NULL,   -- exact decimal digits, or an address
    first_seen_run   BIGINT   REFERENCES runs (run_id),
    PRIMARY KEY (block_number, log_index)
);
