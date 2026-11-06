# Spark income and MSC comparison (v3.10)

`prime-management.spark-reconciled.v3.10` adds reporting income and evidence without changing published MSC calculations. Old v3.9/v3.8 selections retain their original numbers/layout. All figures are revenue/expense flows; asset and depositor principal is excluded.

## Sources and statement rows

| API metric | Method | Scope/limitations |
|---|---|---|
| `investment.psm3.susds` | Published monthly non-venue PSM3 SSR credit; otherwise the same retained raw result's `psm3_susds_appreciation` | No daily replay. Missing fields stay TBD; stale inputs remain partial. BR spread reimbursements remain reductions of Sky funding, not additional revenue. |
| `investment.susds` | Sum of existing raw sUSDS returns plus the PSM3 component | Gross yield includes Savings-funded holdings. An MSC-only fallback cannot certify that entire gross return. |
| `revaluation.sparklend_assets` | Existing independently reconciled daily asset price revaluation | Capital and supply interest are separated by the existing source bridge. |
| `revaluation.savings_liabilities` | Signed negative of the existing depositor FX revaluation | This is the liability FX offset, not VSR interest or principal. |
| `gains.sparklend_nonstable` | Gross asset revaluation, including Ops | Depositor FX is excluded from asset gains and belongs to Savings funding. |
| `funding.savings` | VSR interest plus depositor FX expense | Liability FX is deducted here once, separately from VSR interest and principal. |
| `investment.anchorage` | Existing attributed receipts/principal exclusions, with reviewed March/April onchain cash evidence | March cash received at the ALM is 805,479 USDC; April is zero. Spark's 133,500 funding transfer into escrow is not income. April third-party payments are disclosed, not guessed as fees or negative interest. Loan accrual terms remain unconfirmed. |
| PYUSD/RLUSD/USDG income | Existing issuer recognition rules | PYUSD/RLUSD accruals and USDG receipts remain distinct from MSC cash recognition. |
| SparkLend fees | Treasury-to-ALM/Ops receipts, including nonstable reserve tokens at receipt value | Original receipt months remain recognized once. September's historical MSC true-up is a control, not extra reporting income. |

Each new numeric statement row has an explicit API metric and API-owned indentation. Existing immutable sources, decimal precision, source references and quality/coverage metadata are preserved. The reporting-only reviewed chain snapshot is immutable and hash checked; update it only with a new policy/snapshot. No RPC endpoints or credentials are stored.

## API

- `GET /v3/reporting/statements?prime=spark&year=2026`: upgraded statement, `spark_msc_reconciliation`, reviewed cash evidence and fee true-up control.
- `GET /v3/reporting/spark/msc-reconciliation?year=2026`: annual monthly bridge.
- Add `month=2026-04` to select one month.
- Add `revision=<statement selection>` to replay the exact selected sources. A selection older than v3.10 cannot supply this bridge and returns 404. A month outside the selected year returns 422.

GET requests read retained data only. Historical published PSM3 credits and the March/April evidence become available with the projection; no daily backfill is needed. Recent PSM3 income requires the existing retained raw credit field. No database migration or MSC replay is required.

## Reconciliation

The bridge starts from the MSC supply-side and demand-side headlines and includes:

1. Savings interest, separately deducted in reporting.
2. Per-venue allocation and recognition differences, grouped by exact identities.
3. Sky funding comparison and the published per-venue/headline rounding control.
4. Reporting fees, other rewards and net non-stable revaluation.

`quantitative_bridge_residual` checks the arithmetic of the allocation bridge. A near-zero result is not economic certification: native-return differences, escrow payment purposes and incomplete inputs still need review. Bridge rows therefore remain `review_required`; missing MSC summaries are `unavailable` and have no manufactured comparison amounts. No balancing plug is inserted.

The September fee control compares original January–August reporting fee receipts against 2,392,354.07 USDS. It is not applicable to report cutoffs before September 30 and never contributes to reporting revenue. Operating expenses are outside the MSC-to-total-net-revenue bridge.

The dashboard displays this bridge on Spark's income statement, with source-aware values and a month selector. Other primes retain their existing reporting policies.

The agreed accounting contract is [ACCOUNTING_RULES.md](../src/settle/revenue/ACCOUNTING_RULES.md). Treasury reward and fee receipts are tracked separately from subsequent gains and cash sale proceeds. Historical SDE ownership is recalculated for reporting without changing MSC. Contributor SPK remains an explicit missing expense component until the approved monthly USD expense figures arrive.

Backfill the new sources with `python -m settle.revenue.spark_reporting_job --from 2026-01-01 --to 2026-10-31 --accounting-only`, then replay `python -m settle.revenue.cash_flow.job --from 2026-01-01 --to 2026-10-31` using the authorized environment. These jobs append reporting revisions; they do not replay MSC.
