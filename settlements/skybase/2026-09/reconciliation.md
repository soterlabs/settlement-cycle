# Skybase September 2026 DR reconciliation

Upstream: [`settle-dr-dune@ed08241`](https://github.com/soterlabs/settle-dr-dune/commit/ed08241beb57cebe60dcc2b278481cc2b68d795e), merged PR #28.

This refresh imports the finalized September workbook and full-precision CSV. It does not replay RPC/indexer inputs or recalculate the agent rate. Published January-August reports and every other prime report are unchanged. Consolidated September Sky/TMF reports are reaggregated from the saved reports and inputs. API data is unchanged.

## September-earned revenue

PR #28 applies the Grove XR schedule to Pendle and Morpho: 0.5% through July 8, then 0.2% from July 9. September uses the daily-compounded 0.2% rate across 30 days, replacing the old flat 0.2% / 12 monthly calculation. Historical catch-ups remain separate from these earnings.

| Code / venue | Previous September DR | Updated September DR | Change |
|---|---:|---:|---:|
| 1997 / Pendle | 15,124.246928 | 14,902.209045 | -222.037883 |
| 1998 / Flagship | 5,262.771706 | 5,185.509367 | -77.262339 |
| 1999 / Risk Capital | 20.649651 | 20.346495 | -0.303156 |

| Component | Previous | Updated | Change |
|---|---:|---:|---:|
| September DR | 106,035.353069 | 105,735.749692 | -299.603378 |
| Agent rate | 38,076.005920 | 38,076.005920 | 0.000000 |
| September-earned total | 144,111.358990 | 143,811.755612 | -299.603378 |

Grove Farm retains its emitted-code split: 1/1002 to Skybase, 2009 to Grove, and -999999 unpaid. Code 1020 remains 1inch; 99/10000/10001 remain non-payable. All unrelated upstream differences are below 1e-9 USDS and arise from CSV float serialization.

## Historical payments carried in September

The updated January-August calculations from upstream PR #28 replace the
earlier fixed true-ups under the operator instruction. Each amount is rounded
to six USDS decimals and carried as a separate September payment item, not
September-earned revenue. The Grove Farm true-up is unchanged.

| Item | Previous true-up | Updated true-up | Change |
|---|---:|---:|---:|
| Pendle / code 1997 | 27,740.235315 | 41,560.042993 | +13,819.807678 |
| Flagship / code 1998 | 34,229.172646 | 71,804.679106 | +37,575.506460 |
| Risk Capital / code 1999 | 758.752668 | 1,782.888077 | +1,024.135409 |
| Grove Farm / codes 0/1/1002 | 61,966.169912 | 61,966.169912 | +0.000000 |
| **Total** | **124,694.330541** | **177,113.780088** | **+52,419.449547** |

| Payment bridge | USDS |
|---|---:|
| September-earned revenue | 143,811.755612 |
| Separately identified historical true-ups | 177,113.780088 |
| **Total September payment** | **320,925.535700** |

## Validation and reproduction

The prime-report refresh is limited to `settlements/skybase/2026-09/`; the dependent September Sky total and TMF reports are also updated. The refresh is idempotent; all non-DR calculation fields are unchanged, the four true-ups each appear once in the workbook, and all unrelated settlement artifacts retain their exact file hashes.

```sh
PYTHONPATH=src python scripts/run_skybase_2026.py --dr-only --months 2026-09
```

The frozen snapshot takes precedence over the submodule workbook, so both were updated together. `data/distribution_rewards/2026-09/manifest.json` binds the source files to the upstream commit. The CSV is reconciled to the workbook to its half-cent rounding precision; amounts in the payment bridge retain full precision in provenance.

- Workbook SHA-256: `17e8ed88d00a53c3a95371ecb9d715569542e8ece2f51aa96994c7174de16ca3`
- September CSV SHA-256: `a3f1a04959a241d8986bb8e2cde24ece4792f149e798c34fb439ee401d89520a`

## Consolidated Sky net revenue and TMF

The full **177,113.780088 USDS** true-up is recognized as demand-side expense
for Sky in September. These earnings were not booked in the published
January-August reports, which remain unchanged. Keeping their earned-period
label separate does not justify omitting the expense. Each item is explicitly
marked `recognize_sky_expense: true`; payments already expensed previously
would leave that flag false and must not create a second expense.

| Sky net revenue bridge | USDS |
|---|---:|
| Before the Skybase DR refresh, with true-ups incorrectly omitted | 14,812,762.211316 |
| Lower September-earned DR, after normal payment rounding | +299.000000 |
| Recognize all previously unbooked historical true-ups | -177,113.780088 |
| **Corrected September Sky net revenue** | **14,635,947.431228** |

The normal-accrual Skybase payment is rounded from 143,811.755612 to 143,812
in the consolidated preview, following the existing whole-USDS convention.
The separately disclosed true-ups retain their exact six-decimal payment
amounts and are subtracted once in the MSC leg. Thus the Sky report's combined
rounded payment is 320,925.780088 versus the unrounded Skybase payment of
320,925.535700; the 0.244388 difference is entirely normal-payment rounding.

TMF reuses the original September activity, month-end state, TWAP, backstop
capital and supply inputs. It uses **14,635,947** whole USDS of Sky net revenue.
Proposed hop is **2,693 seconds**, vestTot is **114,797,518 SKY**, and the
Core Council Buffer transfer rounds to **2,927,189 USDS**. Actual September
execution and the **7,372,287.576422652922647064731 SKY** burn amount are unchanged.

Audit: `docs/september-2026-close/skybase-dr-refresh-downstream.json`.
