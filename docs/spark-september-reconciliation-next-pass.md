# Spark September: corrected RLUSD replay and remaining funding controls

Status 2026-11-10: corrected offline replay running, not published.

## RLUSD correction

`scripts/patch_spark_rlusd_history.py` adds Sentora (S72) and SparkLend RLUSD
(S70) receipt movements to 2,532 transactions in the saved history. It checks
closing Sentora shares and spRLUSD scaled/nominal balances against the existing
pinned contract proof. Treasury receipts are income, not borrowed principal;
Mint-on-withdraw is a negative scaled movement. Unknown receipt payers, duplicate
attachment, conflicting logs and missing cash transactions are rejected.

The existing cash movements and debt draws/repayments are unchanged. Future
ordinary captures use the venue configuration proposed separately in PR267.
This input migration repairs the historical research capture without changing
published settlement revenue or using the old residual as a new venue cost.

## Replay and follow-up

The persistent `spark-rlusd-reconciliation` tmux session runs
`scripts/replay_spark_rlusd_reconciliation.py` from this checkout. Inputs are
saved under `/tmp/spark-september-financing-results`; output is separate under
`/tmp/spark-september-rlusd-replay`. The replay forbids socket connections,
checks input/source hashes and saves detailed PSM3 and saver-interest financing
funding in addition to selected venue balances. It reprices existing venues
using the existing policies and gives S70/S72 explicit research cost treatment.
All 56 previously priced venue amounts reproduced in the old-input preflight.

Watch `status.json` and `run.log` in the output directory. A companion persistent
`spark-rlusd-followup` session waits for `completed_not_published`, then runs
`scripts/analyze_spark_custody_funding.py` and writes `custody-analysis.json`.
A failed replay stops that follow-up. No automatic publication is configured.

The custody analysis separates full PSM3 value into Sky principal, Savings
principal by vault, and unclassified value. The last component is NOT assumed
to be earnings. Saver-interest accounts hold financing principal after their
cash has been spent, under the retained proportional-funding convention. Their
zero asset value is deliberate, not proof that debt disappeared. Neither these
expense accounts nor PSM3 are added as investment venues.

## Smaller residual: CCTP is accounted for

The previous replay's $510.495337 CCTP cost equivalent covers 38 custody accounts:

- 34 burns were matched with receipts inside September; their normalized cash
  movements net to zero and their closing Sky basis is zero. September transit
  cost equivalent: $455.835929.
- Four late September 30 sends were received on October 1, independently checked
  using the authenticated source domain, nonce, messenger, token, owner, amount,
  recipient and destination domain. September cost equivalent: $54.659408.

| Source domain / nonce | Destination | Arrival UTC | Receipt transaction |
|---|---|---|---|
| Base / 814700 | Ethereum | Oct 1 00:11:11 | `0x523007ae839050836cc035078a2db6bfe05951c5b7ca718f5749b483c5633770` |
| Base / 814701 | Ethereum | Oct 1 00:11:11 | `0x523007ae839050836cc035078a2db6bfe05951c5b7ca718f5749b483c5633770` |
| Base / 814702 | Ethereum | Oct 1 00:16:11 | `0x27544d806435025ed9ef0f02f25fa2edfa03290052e39619d13bf7dd84d7e886` |
| Ethereum / 502084 | Base | Oct 1 00:01:45 | `0x7ba35137e9a70e306788acb4a71241631a6eabdc89efa1f61c53de6a3ba22b8d` |

Evidence is under `reconciliation/spark_bridge_controls_2026_09/`. This resolves
the nature of those custody balances. It does not remove financing during
transit, assign them to an arbitrary investment venue, or revise September's
closing balances to include October receipts. Their corrected costs must come
from the running replay, not be copied from the previous one.

The remaining old unpriced $69.455952 mostly consists of three June native
bridge claims ($61.539482 combined), plus small B2C2/Binance/Paxos entry-point
balances. Pinned Ethereum calls at block 26,093,737 confirm that all three
claims remain unfinalized/unrelayed at September 30 (Base portal finalization
flag; Optimism and Unichain messenger successful-message flags all return zero).
This explains the $61.539482 as retained bridge custody, rather than a missing
investment allocation. Evidence: `native-pending-september.json`. The ~$6,462 unmatched-outflow component still needs
individual investigation; exchange execution differences are candidates, not
confirmed expenses.

## Validation

17 focused tests pass across receipt migration, custody-value decomposition,
deduction credits and saved-stock reconciliation. No global reconciliation gap
is claimed closed before the corrected replay and funding reviews finish.
