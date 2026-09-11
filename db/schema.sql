-- Raw-data store for the MSC extract layer.
--
-- One generic table keyed by (source, args_hash). Append-only; never updated.
-- Historical raw data is immutable — a new oracle / position adds new rows
-- but never mutates existing ones (enforced by UNIQUE + ON CONFLICT DO NOTHING
-- on insert paths). Compute reads from this table via the read-through cache
-- in ``src/settle/extract/cache.py``.
--
-- ``source``      e.g. ``chronicle.read``, ``rpc.eth_call``, ``dune.execute``
-- ``args_hash``   SHA256 of the canonical args (same key as the on-disk pickle cache)
-- ``args``        JSONB of the canonical args — readable for ad-hoc queries
-- ``payload``     JSONB of the fetched value, using lossless envelopes for
--                 Decimal / bytes / datetime / tuple (see postgres_store.encode_payload)
-- ``fetched_at``  when this row was inserted

CREATE TABLE IF NOT EXISTS raw_data (
    id          BIGSERIAL    PRIMARY KEY,
    source      TEXT         NOT NULL,
    args_hash   TEXT         NOT NULL,
    args        JSONB        NOT NULL,
    payload     JSONB        NOT NULL,
    fetched_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (source, args_hash)
);

CREATE INDEX IF NOT EXISTS idx_raw_data_source     ON raw_data (source);
CREATE INDEX IF NOT EXISTS idx_raw_data_fetched_at ON raw_data (fetched_at);


-- ---------------------------------------------------------------------------
-- HyperSync log store (Envio HyperSync-direct sources).
--
-- Unlike ``raw_data`` (an opaque content-hash blob cache), this stores decoded
-- log ROWS relationally, keyed by an immutable ``(stream, block_number,
-- log_index)``. A "stream" is one HyperSync log selection (chain + addresses +
-- topic filters), hashed to a stable id (see hypersync_store._stream_key).
--
-- STALENESS GUARANTEE: rows are only ever persisted for blocks at or below
-- ``chain_head − HYPERSYNC_REORG_MARGIN`` — i.e. finalized data that cannot
-- reorg. A query whose upper bound sits inside the reorg window is served live
-- and NOT written. Block-pinned facts are immutable, so a stored row is never
-- stale; re-runs at the same/earlier pin read straight from here, and a later
-- pin fetches only the incremental block range.
CREATE TABLE IF NOT EXISTS hypersync_logs (
    stream        TEXT     NOT NULL,   -- sha256(chain, addresses, topics)
    block_number  BIGINT   NOT NULL,
    log_index     INTEGER  NOT NULL,
    block_time    BIGINT   NOT NULL,   -- unix seconds (UTC)
    address       TEXT     NOT NULL,
    topic0        TEXT,
    topic1        TEXT,
    topic2        TEXT,
    topic3        TEXT,
    data          TEXT     NOT NULL,
    -- Populated only for streams whose caller requested "transaction_hash"
    -- (those streams carry the field set in their key, see
    -- hypersync_store._stream_key); NULL for the default field set.
    transaction_hash TEXT,
    PRIMARY KEY (stream, block_number, log_index)
);
ALTER TABLE hypersync_logs ADD COLUMN IF NOT EXISTS transaction_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_hypersync_logs_stream_block
    ON hypersync_logs (stream, block_number);

-- Contiguous block range already fetched + persisted per stream. A query for
-- [from, to] within [covered_from, covered_to] is served entirely from the DB;
-- otherwise only the missing sub-ranges are fetched from HyperSync.
CREATE TABLE IF NOT EXISTS hypersync_coverage (
    stream        TEXT         PRIMARY KEY,
    covered_from  BIGINT       NOT NULL,
    covered_to    BIGINT       NOT NULL,   -- always ≤ chain_head − reorg_margin
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);


-- ---------------------------------------------------------------------------
-- Daily pipeline + read API (docs/PRD_daily_pipeline_api.md).
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
