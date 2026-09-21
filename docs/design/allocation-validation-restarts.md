# Restartable allocation validation

This diagnostic command replays capital history without regenerating published
settlements. It does not certify cost-of-funds reconciliation or APY correctness.
It depends on the allocation analytics implementation in PR #215.

With `DATABASE_URL` set to a writable PostgreSQL database, run:

```sh
.venv/bin/python -u scripts/validate_allocation_capital.py spark \
  --start 2026-08-01 --end 2026-08-31 \
  --output-dir /tmp/spark-august-validation
```

Repeat the same command after correcting a failure. Use a separate output
directory for every prime/period, and only one process per output directory.
`pins.json` preserves the end-of-period block pins. `status.json` records running,
failed, or completed; `result.json` contains replay counts and unsupported venues.
A completed replay is not a passed reconciliation. Outputs are atomically
replaced. A killed process can leave status at `running`; check the process/log
before restarting. Old result files are valid only when the latest status is
`completed`.

HyperSync's durable store commits missing history in at most 2,000,000-block
intervals, configurable with positive `HYPERSYNC_CHECKPOINT_BLOCKS`. Successful
finalized intervals survive a subsequent failed download. Retries query missing
intervals only; unfinalized data remains uncached under the existing reorg guard.
The interval containing a failure is retried. Smaller intervals reduce download
work lost on interruption but increase request overhead. This bounds blocks,
not bytes: monitor the database filesystem during large historical scans.

The command requires persistence rather than silently falling back to an
uncached scan. Normalization is replayed from the beginning using cached raw
logs and RPC reads; mutable accounting state is not restored from a potentially
incompatible snapshot. Accounting errors are not skipped or retried blindly.

Spark's historical JTRSY gateway `0x36036ffd9b1c6966ab23209e073c68eb9a992f50`
returns JTRSY from `share()` and USDC from `asset()` at block 22,217,641.
The venue configuration omits its underlying. The async capital adapter now
resolves missing metadata from authenticated `asset()` calls only for known
stablecoins. Unknown assets and conflicts with explicit configuration still fail.
