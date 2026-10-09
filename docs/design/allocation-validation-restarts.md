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
uncached scan. Normalized inputs are saved in compressed `history.jsonl.gz` before replay.
The snapshot fingerprint covers normalization, extraction and domain code,
prime configuration, block pins and the input revision. Those changes invalidate
it; compute-only fixes can reuse it. Mutable accounting state is never cached:
every restart replays the normalized events from inception. Accounting errors are not skipped or retried blindly.

Spark's historical JTRSY gateway `0x36036ffd9b1c6966ab23209e073c68eb9a992f50`
returns JTRSY from `share()` and USDC from `asset()` at block 22,217,641.
The venue configuration omits its underlying. The async capital adapter now
resolves missing metadata from authenticated `asset()` calls only for known
stablecoins. Unknown assets and conflicts with explicit configuration still fail.


After capital replay, compare allocation costs against a published control without
regenerating that settlement:

```sh
.venv/bin/python scripts/validate_allocation_financing.py \
  --provenance settlements/spark/2026-08/provenance.json \
  --history-dir /tmp/spark-august-validation
```

The command verifies prime/period/pins and snapshot freshness, reads the existing
revenue and daily rates, and writes `financing.json` beside the history snapshot.
The control's SHA256 is recorded and its bytes are checked unchanged. Daily idle
deductions are read through the existing settlement helpers. Per-ilk attribution
for multi-ilk primes remains incomplete and is explicitly labelled combined.
The diagnostic distinguishes `source_complete` from `complete`: reconciliation
is complete only when source coverage is complete AND costs agree within a cent.
Daily basis controls separate unattributed debt, deduction differences, and the
global nonpositive-utilization floor. No balancing amount is inserted into
allocation costs, and neither a replay success nor a component explanation is a
claim of passing reconciliation.

## Maple multi-request redemption regression

Spark transaction `0x2f55ad12285fd8bc8240e78afe76aaedaa75ba45056fca50cdd722ce8a416edc`
(block 25,351,486) settles two syrupUSDC queue requests:

| Request | Raw shares (6 decimals) | Raw USDC (6 decimals) |
|---|---:|---:|
| First | 3,926,898,847 | 4,592,589,243 |
| Second | 2,848,712,244 | 3,331,627,760 |

Using the first cash/share ratio to mark the whole exit gives $7,924.2170026994,
while actual cash totals $7,924.217003. The strict ledger rejected the roughly
$0.0000003006 overdraw. Normalization now aggregates redeemed shares and actual
cash by queue/owner within the transaction before calculating the proportional
basis release. This handles full and partial exits without weakening the
ledger's source-value invariant.

Fresh August OBEX/Osero replays completed after the fix. Their allocation/global
cost gaps remain $52,902.0964896 / $0.7206011. Independent component checks explain
them as follows (global minus allocation costs):

| Component | OBEX | Osero |
|---|---:|---:|
| Charges on capitalized debt | 52,902.0952441 | 0.7484525 |
| Cash funding / other cash deductions | 0.0012455 | -0.0278514 |
| Lending idle input difference | 0 | 0 |

The gap is explained, not resolved. Capitalized-debt attribution and multi-ilk
allocation reconciliation remain incomplete. Published controls are unchanged.

Replay also pools sub-cent residuals by chain while retaining their exact value
and borrowed basis. Material unmatched outflows keep their transaction identity.
Repayments funded wholly by borrowed principal skip the unnecessary global
own-money refinancing scan, and expired clearing-account uncertainty is removed
after propagation. These changes limit replay work without deleting principal
or treating rounding residuals as revenue. A conservation regression verifies
that repayments still reduce total borrowed basis by the repayment amount.
