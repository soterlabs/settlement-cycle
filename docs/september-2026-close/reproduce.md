# Reproduce the September close

The September close now uses the official **September 30 SOFR of 3.90%**,
published October 1. `config/september_2026_official_reference_rates.json`
preserves the complete official September snapshot and its content hash.

The earlier 3.88% carry-forward snapshot remains available only for explicit
reproduction of the superseded estimate via `--allow-september-sofr-carry`.
It is no longer the default input or the rate used in the current reports.

With the normal RPC/indexer environment configured, run from this repository:

```sh
PYTHONPATH=src python scripts/run_september_close.py \
  --include-protocol
```

This calculates all six primes through the full monthly pipeline, independently
rechecks their borrowing costs, writes September reports, then runs September
non-MSC, consolidated Sky and TMF in dependency order. Only September artifact
paths are written. It imports the finalized DR snapshot; it does not replay DR
or publish API data. For selected primes omit `--include-protocol` and use, for
example, `--primes spark grove`.

The ordinary API-to-monthly reuse command also supports the explicit exception:

```sh
PYTHONPATH=src python -m settle monthly-from-revenue \
  --prime spark --month 2026-09 --revision REVISION_ID \
  --output-dir /path/to/new-empty-directory --allow-september-sofr-carry
```

All existing revision, code/configuration, input and borrowing-cost checks still
apply. An old revision from a different code/configuration version is not made
compatible by this flag: recalculate it first, or use the full monthly command
above. Without the flag, incomplete reference snapshots continue to fail.
The exception accepts only the exact pinned snapshot for Spark/Grove September;
other missing dates, changed rates, altered carries, and other primes/months
are rejected. The automatic daily publisher and SOFR holiday calendar are
unchanged.

Gelato verification now requires the actual `Vat.move(join, Vow, amount)` cash
entry with the same transaction hash as its `Blow` settlement. Both HyperSync
and the SQL source retain transaction and log identifiers. Missing cash,
unrelated same-day deposits, or duplicate cash log identities fail the close.
The normal receipt and settlement accounting remains unchanged.
