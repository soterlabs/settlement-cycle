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
    PRIMARY KEY (stream, block_number, log_index)
);

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
