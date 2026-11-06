# Restartable capital normalization

`validate_allocation_capital.py` now saves normalized transactions every 1,000
transactions per chain under `<output-dir>/normalization/`. Each compressed JSON
chunk contains its output batches and the adapter state after its final transaction:
raw holdings, Maple queues/managers, async subscriptions/redemptions, NFT liquidity
and pending collections, PSM shares, custody accounts and verified CCTP burns.

A restart reloads raw logs and rediscovers NFT metadata, verifies the input digest,
then skips pricing and normalizing committed transactions. Completed chains also
skip transaction normalization. Cross-chain and custody linking run afterward,
so a restart does not reuse partly applied links or duplicate their effects.
The final `history.jsonl.gz` remains the existing replay input format.

Chunks and their manifest are atomically replaced and fsynced. The manifest is
committed last: a chunk written before a crash but not referenced by the manifest
is ignored. A directory lock prevents concurrent checkpoint writers. Checksums,
transaction identities and contiguous coverage are checked before reuse.

The identity binds normalized source/configuration fingerprints, settlement pins,
raw logs, Decimal settings and a hash of the database configuration. Changed
inputs or damaged chunks fail explicitly; use a new output directory when changing
normalization policy, source revision or pins. Decimal's temporary arithmetic flags
are intentionally excluded. Checkpoints contain typed JSON, not executable pickle.

## Usage

Repeat the same command and output directory to resume:

```sh
PYTHONPATH=src python scripts/validate_allocation_capital.py spark \
  --start 2026-09-01 --end 2026-09-30 --capture-only \
  --block-resolver rpc \
  --provenance reconciliation/monthly-allocation-controls/spark/2026-09/provenance.json \
  --output-dir /path/to/spark-september
```

`--checkpoint-size N` controls the commit interval. At most N uncommitted
transactions need re-normalizing after a crash, plus log discovery and final
linking work. `--no-checkpoints` disables partial-history reuse. Neither option
changes accounting or publishes API/settlement data.

## Fewer database round trips

The CLI also wraps normalization in `postgres_store.memoized_reads()`. Repeated
requests for the same durable source/revision and argument hash reuse a previously
successful database read. It retains at most 32,768 entries and 64 MiB of serialized
payload/key bytes (Python object overhead is separately bounded by entry count).
Each hit returns freshly decoded data, so caller mutations cannot leak between calls.

Misses, failed reads and unverified writes are not memoized. Connection or database
changes clear entries. Required database-availability and durable-write checks still
apply. The context is discarded after capture and logs hit/miss counts; no bulk
preload or unbounded copy of the raw-data table is performed. Unique first reads
still require PostgreSQL, and provider rate limits remain applicable.

## Rollout and validation

An already-running process cannot acquire these checkpoints retroactively. Leave
the existing September worker running; use this version on its next start or a
separate future capture. Existing verified raw inputs remain reusable, but the
first run of this version must build its own normalization checkpoints.

Tests interrupt real queue/async-vault flows at a checkpoint, resume and compare
exact normalized histories against uninterrupted runs. They also cover completed
chain reuse, all adapter-state types, orphaned chunks, corrupted/changed inputs,
writer contention and Decimal flags. Memo tests exercise query counts, isolation,
LRU/byte limits, mutable values and persistence failures. Measure production hit
rates before claiming an end-to-end speedup.
