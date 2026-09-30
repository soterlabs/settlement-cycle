# PR #220 review and historical estimate

Reviewed commit: `4733d944cbb66e70da69c5fb840253f1f4225494`.

## Review

No blocking defect found in the new event lookup or source wiring. The lookup includes direct transfers, uses `(SoM, EoM]`, deduplicates logs/blocks, propagates provider errors, and reuses the reorg-safe raw-log store. Explicit callbacks and offline balance fixtures remain authoritative. Non-Cat-C/D paths are untouched.

**Remaining limitation (existing helper): complete exits can omit final-block interest.** In `normalize/positions.py`, the event-row calculation sets `y_evt = 0` when the post-event scaled balance is zero and uses the prior block's balance to infer the capital outflow. A position worth 1,000 before the exit block but redeemed for 1,001 in that block will book -1,000 capital and miss 1 of interest. The new wiring narrows the timing window to a block but does not fix this inherited zero-balance case. Dust exits and raw-unit rounding likewise deserve care. This is why the event-principal estimate below must not be presented as the exact numerical output of PR #220.

Added a regression for an existing position accruing interest both before and after an additional deposit: 1,100 capital and 300 interest, rather than treating pre-event interest as principal. The focused review suite passes 76 tests. GitHub Actions did not start because account payments/spending limits blocked jobs; this is not a failing test assertion.

## Estimate method and limits

On September 30, independently fetched holder-level Mint, Burn and BalanceTransfer logs for configured Spark/Grove/Osero Cat C/D venues over the pinned January–August 2026 artifact ranges. The audit decodes economic capital as:

- Mint: `value - balanceIncrease` (can be negative when a small withdrawal emits Mint).
- Burn: `-(value + balanceIncrease)`.
- Direct BalanceTransfer: signed `scaled value * emitted liquidity index / RAY`.

Duplicate logs are removed; self-transfers net to zero. Divide by the token's decimal scale. Estimated revenue correction is **published period_inflow minus event-derived capital**. Positive means published revenue was understated; negative means overstated.

This isolates the revenue/capital classification difference, holding other components constant. It is not a full historical settlement replay, nor a certified amount payable: time-weighted balances, lending-idle offsets, Sky charges, SDE and other settlement components have not been recomputed. It also does not mix in reserve-factor revenue changes from other PRs. Raw-unit rounding is retained at high precision and reporting rounds to cents.

Historical estimates were explicitly requested after the original forward-only implementation request. No published settlement, API revision or production configuration has been modified; implementation rollout remains prospective.

## Monthly estimated revenue corrections (USD)

Positive adds revenue; negative removes revenue. A dash means no audited published period.

| Month | Spark | Grove | Osero |
|---|---:|---:|---:|
| 2026-01 | +221,360.79 | +742.91 | — |
| 2026-02 | -523,330.59 | +661.88 | — |
| 2026-03 | -349,918.28 | -1,121.93 | — |
| 2026-04 | +326,795.72 | +0.00 | — |
| 2026-05 | -269,058.54 | +0.00 | — |
| 2026-06 | -137,963.68 | +0.00 | — |
| 2026-07 | -228,004.44 | -0.00 | +35.81 |
| 2026-08 | -129,177.47 | +0.00 | +234.84 |
| **Net** | **-1,089,296.48** | **+282.87** | **+270.64** |

The audit covered 22,701 unique events across 122 allocation-months. Osero principal is independently recovered as exactly 1,000,000 USDS in July and 13,001,000 USDS net in August. Its corresponding revenue estimates are 426.11130062229413903900 and 5,792.65585861536706733200. All audited rows have zero SDE share; the revenue adjustment flows to prime revenue with other components held fixed. These results reproduce the earlier note to the reported cents.

Full review validation: 1,196 unit/monthly integration tests passed, 7 optional dependency/workbook skips. New audit arithmetic tests cover small withdrawals emitted as Mint, interest-only Mint, Burn, and direct/self transfers.

The accompanying JSON contains allocation-level results, source artifact hashes and event-file hashes. The raw event captures remain local under `/tmp/atoken-review-impact`; they are not published settlement artifacts. Reproduce independently with `PYTHONPATH=src python scripts/audit_atoken_event_principal.py --settlements /path/to/settlements --output /tmp/atoken-audit`, with `ENVIO_API_TOKEN` configured. The script only reads provider data and writes to the specified audit output directory.
