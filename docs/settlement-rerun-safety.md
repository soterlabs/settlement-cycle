# Saved settlements and reconciliation reruns

## Status of closed PR #187

[PR #187](https://github.com/soterlabs/settlement-cycle/pull/187) proposed a
2026-08-01 cutoff for the nominal-APR rate convention. It was closed, not
merged, on September 2, 2026. Its closing comment chose an operator policy:
methodology changes go forward, and published months are not regenerated.

The underlying overwrite risk remained on main at `ef2c680`: `_helpers.py`
still supplies the nominal calculation introduced by
[PR #175](https://github.com/soterlabs/settlement-cycle/pull/175) on September 1,
and `write_settlement` replaced existing provenance, summary and workbook
files without checking the destination. A rerun for a venue fix could therefore
replace a historical report with figures calculated under today's methods.
The monthly-from-revenue command already required an empty destination; the
ordinary CLI, live runner and shared writer did not enforce equivalent safety.

This change enforces the closing policy in the shared writer rather than
reviving the rejected date gate. An existing summary, provenance file or
settlement workbook is protected by default, regardless of whether its
publication status is known. The CLI and live batch runner check destinations
before source reads; the writer checks again before enrichment or file writes.
A batch containing any protected destination fails before producing its first
month's artifacts. Duplicate primes/months and invalid calendar months are
also rejected before any batch work starts.

The shared writer reserves the destination using an atomically created
`.settlement-write.lock` and rechecks saved artifacts after acquiring it. The
lock covers enrichment and all artifact writes, including the workbook build;
an explicit overwrite cannot bypass another writer's reservation. Ordinary
failures release the lock, while any partial artifacts remain protected. If a
process is killed before cleanup, the error identifies the lock path and the
file records its PID. Verify the process has stopped before removing a stale
lock; the writer never automatically steals one.

## Compare historical months without replacing them

For one month, use an empty output directory:

```sh
settle run --prime grove --month 2026-07 \
  --output-dir /tmp/grove-reconciliation/2026-07
```

For January through September:

```sh
python scripts/run_live_2026.py --primes grove \
  --months 2026-01,2026-02,2026-03,2026-04,2026-05,2026-06,2026-07,2026-08,2026-09 \
  --output-root /tmp/grove-reconciliation
```

The live runner writes `ROOT/<prime>/<month>/`. Without `--output-root`, it now
honors the existing `SETTLE_OUTPUT_DIR` root override, then falls back to the
repository's `settlements/` directory. Reusing an output folder containing
settlement artifacts also needs an explicit replacement choice.

If replacing saved artifacts is intentional, pass `--allow-overwrite` to
`settle run` or the live runner. Direct Python callers use
`write_settlement(..., allow_overwrite=True)`. This is an explicit replacement,
not evidence that a restatement was approved or that methodology changes are
an appropriate reconciliation credit.

## What the protection establishes

Historical calculations still use the current methodology. A reconciliation
rerun is a comparison under that methodology, not a reproduction of the old
settlement. Differences must be attributed to rates, source changes and the
specific correction before any amounts are reconciled.

The original #187 also documented mixed conventions in the saved Spark
June/July and Grove July records. A single date cutoff cannot reproduce every
one of those records. This change makes no choice about reversing previous
repricing, adds no cutoff or historical exception table, and rewrites no
settlement artifacts. Exact historical rate reproduction remains separate
work requiring an agreed baseline for the affected prime/months.

Low-level renderers and the explicit DR-only refresh workflow are not a full
settlement recomputation and retain their existing behavior. Provisional
`as-of/` previews remain separate from saved monthly reports. The reservation
coordinates callers of the shared writer; it does not lock low-level renderers
or external tools and is not a publication-status registry.
