# September monthly allocation analytics

The dashboard's **Prime Agent Revenues → Yields per venue** view defaults to
September 2026. The API publishes reviewed monthly artifacts separately from
daily revenue and settlement reports. Missing costs remain null, including
unresolved Spark Savings funding and missing Grove Basin deduction attribution.

September controls in `reconciliation/monthly-allocation-controls/{prime}/2026-09/`
are isolated provenance exports of `refresh_september_selected_venues.refresh()`.
That existing offline function reconstructs the reviewed close from hash-pinned
baselines and the approved Spark S1/Grove E10 corrections. These exports do not
regenerate or replace published settlement reports. September 30 uses the final
SOFR input already embedded in that close.

## Reproduce inputs

Use the production PostgreSQL cache and RPC/indexer configuration. For each of
`spark` and `grove`, capture normalized movements at the settlement's block pins:

```sh
PYTHONPATH=src:scripts .venv/bin/python scripts/validate_allocation_capital.py spark \
  --start 2026-09-01 --end 2026-09-30 --capture-only --block-resolver rpc \
  --provenance reconciliation/monthly-allocation-controls/spark/2026-09/provenance.json \
  --output-dir /tmp/september-allocation-inputs/spark
PYTHONPATH=src:scripts .venv/bin/python scripts/validate_allocation_financing.py \
  --provenance reconciliation/monthly-allocation-controls/spark/2026-09/provenance.json \
  --history-dir /tmp/september-allocation-inputs/spark --capture-idle-only --block-resolver rpc
```

Repeat for Grove. Completed histories are hash-linked to their period manifest.
Captures reuse persisted logs/RPC reads after a retry. Chains
absent from the settled period are explicitly unsupported. Issuer-incentive
receivables sharing another venue's token are income rows, not duplicate capital.
RPC timestamp lookup is available when the indexer is rate-limited.

## Calculate and review

Run `scripts/report_allocation_capital_readiness.py` with `--month 2026-09`,
`--settlements-root reconciliation/monthly-allocation-controls`, both histories,
both idle-deduction files, and `--apply-capital-adapters` for fresh captures.
Then run `scripts/report_allocation_yields.py` with the same month/control root
and the resulting readiness artifact. Both require explicit output/Markdown paths.

Principal carried through normalized transfers excludes gains and rewards. This
uses the agreed Sky-funded-except-Savings assumption, without replaying proof of
funding origin. Costs reuse the settled daily effective rates and available exact
deductions. Loan APYs use outstanding facility claims, never escrow cash or a
stale configured notional. Measured position values take precedence over fixed
notionals for on-chain allocations. No known Anchorage loan exposure means no
loan APY estimate.

Review source hashes, coverage/null reasons, revenue agreement with the close,
and material yields before invoking `scripts/publish_monthly_allocations.py`.
Publication writes only monthly allocation API artifacts; it does not change
settlement amounts, global borrowing costs, or daily revenue. Verify both prime
endpoints and the dashboard after publication. A dashboard page being available
does not itself mean September inputs have finished or been published.

## Grove validation

September capture contains 2,020 normalized batches before the reviewed routing
adapters. The prepared result contains 45 rows: 34 have a cost (including zero
principal/exempt rows), eight are covered by another allocation, one is income
only, and two Basin rows await deduction attribution. Known allocation costs
sum to $3,673,476.74; this is a partial subtotal, not a global CoF reconciliation.

The September review corrected two denominator choices: E12 uses its measured
$17,786,194.41 average position value, rather than a fixed $25m notional; E42 uses
its reconstructed $349,916,701 average outstanding facility claim, rather than
the older $304,000,001 configured notional. E12 gross/net APYs are approximately
0.88% / -2.78%; E42 gross/net APYs are 5.92% / 2.08%. Idle USDC E15 has a negative
net APY (-3.63%), as expected. Gross outliers E21 (cash distributions) and E22
(negative booked September revenue) reproduce the existing settlement revenue;
the calculation does not replace them with assumed contractual accruals.

## Code review and deferred backfill

At the operator's request, the Spark capture was stopped after saving its log
checkpoints. Further API backfill is deferred; it is not a condition for merging
this code. Grove's previously published September revision remains available;
Spark September has not been published.

Two sequential code-review/fix rounds were completed:

1. Protect provenance inputs from capture-output filename collisions; reject
   invalid measured yield exposure instead of hiding it behind a notional.
2. Record transitive capital-adapter code hashes for fresh captures; reject
   publication when revenue-control hashes are absent.

Validation after both rounds: 464 targeted allocation, API, cache and HyperSync
tests passed. The earlier isolated full unit suite passed 2,278 tests with one
optional-dependency skip. No further revenue calculation or API publication was
performed during these review rounds.

Before resuming Spark publication, investigate the idle-deduction mismatch.
For September 1, the captured venue deductions total $441,886,778.26 versus
$387,809,077.69 in the settled control: a $54,077,700.57 difference in **deductible
capital**, not borrowing cost. The validator rejected this input. Do not scale
it to fit or override the check; establish whether historical configuration,
measurement, or exemption attribution explains the difference first.
