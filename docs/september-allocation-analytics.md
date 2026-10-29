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
deductions. Anchorage's APY uses the outstanding facility claim, never escrow cash
or a prior month's balance. No known loan exposure means no loan APY estimate.

Review source hashes, coverage/null reasons, revenue agreement with the close,
and material yields before invoking `scripts/publish_monthly_allocations.py`.
Publication writes only monthly allocation API artifacts; it does not change
settlement amounts, global borrowing costs, or daily revenue. Verify both prime
endpoints and the dashboard after publication. A dashboard page being available
does not itself mean September inputs have finished or been published.
