# Spark spUSDS and Grove JTRSY: original funding

Diagnostic work in PR #215; no production settlement, revenue API, or borrowing
cost changes. Results describe acquisition capital held at August 31, 2026,
not outstanding ilk debt. Pre-2026 evidence establishes opening funding only.

| Allocation | Capital | Previously traced | Now traced | Remaining | Coverage |
|---|---:|---:|---:|---:|---:|
| Spark S1 spUSDS | 681,732,615.54 | 584,610,359.57 | 662,616,464.85 | 19,116,150.69 | 97.20% |
| Grove E9 JTRSY | 841,108,582.40 | 756,723,688.09 | 839,775,712.20 | 1,332,870.20 | 99.84% |

The improvement is 78,006,105.28 for Spark and 83,052,024.10 for Grove
(161,058,129.39 combined before rounding). Residual capital may include gains
and external funding; it is not automatically missing Sky debt.

## Spark: mixed Savings funding is bounded rather than discarded

The existing opt-in external-funding bound is now applied to S1. In mixed
transactions it deducts all other positive destinations and any unexplained
cash use from the Sky draw before attributing the remainder to spUSDS. External
cash draws are included in the cash-use calculation, so they cannot conceal a
repayment. This uses no provisional Savings principal/interest repayment split.

The three largest former residuals are August 27
[0x7d01…539b](https://etherscan.io/tx/0x7d01efd98d8d6af9d3e6d4f868288fef77c53732028bc3f80373e0f8e9ab539b),
August 25
[0xeb2a…18d1](https://etherscan.io/tx/0xeb2ae7637cf3a9c1f4afba11b05a4138b1666277fbd86f37b125c3c9783018d1),
and August 31
[0xca46…6b5e](https://etherscan.io/tx/0xca462178b647409b08e8cec2267f25ec820e5fe72f362d04a62e91e4389a6b5e).
Their normalized batches show Sky borrowing alongside Savings USDT draw and
other allocation activity. The bound deliberately leaves competing amounts
unattributed instead of assigning the entire Sky draw to S1.

Evidence is the reviewed normalized history, not a new RPC verification of
every receipt. The same bound was previously validated for Base Morpho. The
new report retains every contribution and a ranked surviving residual.

## Grove: JAAA funding survives reallocation into JTRSY

The source group is E9 JTRSY, E8/E20 JAAA across Ethereum/Avalanche, and E15
Ethereum USDC cash, including their registered subscription/redemption custody.
This follows JAAA redemptions through cash into JTRSY; it does not rewrite the
origin when other cash repays ilk debt. Principal and passive gains stay distinct.

Major previously untraced contributions include January 7 and January 13
JAAA-funded subscriptions, and the March 11 50m subscription using existing
USDC. The remaining March contribution is 614,359.83 at August-end; January
13's two remaining components are 318,599.18 and 204,285.96; January 7 leaves
190,759.74. These four account for most of the residual, and require additional
upstream principal-versus-gain attribution.

The review also found that pooling simultaneous, independent custody operations
changes their origin ratios. For example, the August 4, 2025 transaction
[0x4fa9…4749](https://etherscan.io/tx/0x4fa9ac01fe73c9bbf803213177400d10558d19fa3bdbfb14eeb315b503449749)
delivers JAAA shares while placing JTRSY into redemption custody. These are
separate, already-normalized same-venue custody legs, not fungible cash exchange.

`audit_origin_custody_routes.py` separates exact zero-net movements between a
venue and its registered custody account. It retains all movements and leaves
original debt/external funding in the remainder. The resulting source ledger
carries each route's own borrowed ratio. Unknown counterparties, unmatched
amounts and routes not represented by registered custody receive no special
classification. The change is restricted to diagnostic audits.

## Custody-aware residual ranking and month ends

`audit_scoped_allocation_origin.py` maintains residual contribution lots per
account. Weighted-average withdrawals release each lot; internal custody
transfers retain it. After every target event, their sum must equal the
independent local acquisition-cost ledger minus the borrowed-origin ledger.
Incompatible mixed routing fails instead of inventing a residual contribution.

January-August snapshots use only events through each month-end. Tests compare
the January and April snapshots with independent cutoff replays. Compact
fixtures reproduce the full-history capital, origin, monthly rows and residual
rankings exactly. Other tests check unchanged daily capital, unchanged movements
and total debt, disjoint custody routes, idempotence, and no future-event leakage.

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_scoped_allocation_origin.py \
  --history /tmp/spark-adapted-origin-history.jsonl.gz \
  --venue S1 --group S1 --bound-external-funding \
  --start 2026-01-01 --end 2026-08-31 \
  --output reconciliation/spark_spusds_funding_origins_2026_08.json.gz
PYTHONPATH=src .venv/bin/python scripts/audit_scoped_allocation_origin.py \
  --history /tmp/pr215-grove-eoa/history.jsonl.gz \
  --venue E9 --group E9 E8 E20 E15 \
  --start 2026-01-01 --end 2026-08-31 \
  --output reconciliation/grove_jtrsy_funding_origins_2026_08.json.gz
```
