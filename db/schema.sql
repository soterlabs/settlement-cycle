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
