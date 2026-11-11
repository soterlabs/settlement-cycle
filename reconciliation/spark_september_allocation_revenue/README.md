# September Spark allocation accrual refresh

This API-only refresh preserves every borrowing cost and all MSC amounts, including PR275 and PR277. It restores retained S70/S72 investment income, replaces issuer cash with daily accrued RLUSD/PYUSD incentives, and recomputes allocation yields. S67/S68 accrual rows are not counted again.

RLUSD: 4.5% ACT/365 on raw RLUSD, SparkLend unborrowed supply, and the RLUSD leg of open Uniswap V4 positions. Sentora receives only max(4.5% minus fee-net vault APR, 0). PYUSD: DFEDTARL minus 62.5bps plus 1.8% APY, converted to APR with daily compounding; the spread is 2.575% before March 9. Eligible holdings are raw, unborrowed SparkLend supply and Uniswap V4. Curve eligibility ends June 30, so S25 is unchanged in September.

Existing investment revenue is retained. Incentives are not scaled to cash receipts. Eligible balances use the existing UTC end-of-day convention; fee-net Morpho share returns already include performance fee dilution.

| Venue | Accrued incentives | Total investment + incentives | Gross APY | Net APY |
|---|---:|---:|---:|---:|
| S5 | 352,720.45 | 449,931.22 | 4.5025% | 1.9530% |
| S61 | 224,567.87 | 224,969.90 | 2.7684% | 1.6846% |
| S66 | 32,591.85 | 32,501.82 | 1.9953% | 0.6643% |
| S28 | 830,244.06 | 830,244.06 | 4.8001% | 2.2608% |
| S64 | 685,675.89 | 685,675.89 | 4.5941% | 1.0167% |
| S70 | 5,288.00 | 7,688.00 | 3.5648% | 0.1286% |
| S72 | 479,131.96 | 482,806.12 | 4.5910% | 1.1881% |

## Cash reconciliation

September RLUSD earned 1,202,687.698692 versus cash received 1,373,208; receivable/prepayment movement -170,520.301308. PYUSD earned 1,407,532.379861 versus cash received 1,809,960; movement -402,427.620139. No balancing adjustment is applied.

Opening receivable/prepayment balances are not independently verified by these September source documents. Opening and closing balances therefore remain null, explicitly marked `opening_balance_unverified`; this is a verified monthly movement, not a certified inception-to-date receivable reconciliation. Accrued yields do not depend on those balances. No pre-September replay is required by this refresh.

## Evidence and publication

`evidence.json` pins PR277's retained S70/S72 investment data and source hashes. `incentive-accruals.json` pins the published reporting component revision/hash, daily eligible inputs, receipt totals and official daily DFEDTARL observations. All daily accruals are independently recalculated with the existing incentive engine; incomplete dates/components or inconsistent rates/amounts fail. The pinned baseline content digest and current API revision must match before publication.

Run `PYTHONPATH=src python scripts/refresh_spark_september_allocation_revenue.py --output /tmp/spark-september-accrual-preview.json`. Add `--publish` only with the API database configured after review. Only a new immutable monthly allocation analytics revision is inserted; old revisions remain queryable. No settlement or daily API tables are written.

The two review rounds for the expanded scope cover cash removal, duplicate accrual prevention, missing/duplicate daily components, fee-net top-up rates, reference-rate coverage, preservation of borrowing costs and unrelated rows, and explicit unknown opening balances. Existing formula tests cover March 9 and June 30 boundaries.
