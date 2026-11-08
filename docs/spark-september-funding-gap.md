# Spark September 2026: remaining borrowing-cost gap

Research only. No API publication, reassignment of residual costs, or settlement
restatement. The complete saved replay is finished. This diagnostic reads its
snapshots; it does not replay history or fetch blockchain data.

## Result

The independent September borrowing cost is **$8,226,734.61**. Separate MSC debt
costs are **$418,453.22**, and costs on historical Vat rate accrual are
**$231,413.18**. The 56 priced conditional allocation rows total
**$7,175,113.09**, leaving **$401,755.11**.

The residual decomposes as follows (all amounts are September borrowing costs,
not principal balances):

| Component | Cost |
|---|---:|
| Synthetic unmatched-outflow accounts | $369,807.71 |
| PSM custody accounts outside the selected allocations, before deductions | $184,088.20 |
| Savings-interest financing accounts | $50,430.05 |
| CCTP transit accounts | $510.50 |
| Selected accounts without a pricing policy | $69.46 |
| Pooled rounding accounts | $0.01 |
| Applied allocation deductions minus global deductions | −$203,150.81 |
| Cash debt absent from the funding ledger | $0.00 |
| **Total** | **$401,755.11** |

Rounded components can differ from the rounded total by a cent. Full precision
is in `reconciliation/spark_saved_funding_gap_2026_09.json`.

Every day's selected, financing and other account stocks agree with the saved
funding bridge. Their total equals independently reconstructed cash draws less
repayments. This establishes numerical completeness of cash-debt principal,
**not correct economic attribution of every account**.

## Main unresolved item: RLUSD outflows

Transactions containing an outgoing RLUSD leg account for **$358,974.57** of
the synthetic unmatched-outflow cost. Transactions with only an outgoing RLUSD
leg account for **$298,154.09**. The mixed-asset transaction amounts cannot all
be assigned to RLUSD without resolving their other legs.

The synthetic key `unallocated:<chain>:<transaction>` means the replay had cash
left after matching that transaction's observed destinations. It is not a
recipient wallet or proof that the money left Spark permanently. These accounts
retain borrowed basis until a supported destination/return is identified.

Examples from the saved normalized history:

| Date | Transaction | Observed RLUSD outflow | September residual cost |
|---|---|---:|---:|
| Sep 14 | [0x3bb72d…](https://etherscan.io/tx/0x3bb72d788410d84710283cb7d21b3810dd7ebee9e19a326caea24f702c7d37f7) | 10,000,000 | $15,983.78 |
| Sep 14 | [0xbb31c3…](https://etherscan.io/tx/0xbb31c35cbfa6aa02ab4287bb17c098e80a59d44d370223b36dd1ba55edd52196) | 10,000,000 | $15,983.16 |
| Sep 14 | [0x5c949b…](https://etherscan.io/tx/0x5c949b68dc4e153ba4301e0dcc1f725176b60bf8f47d4ef9a5c4cf89eac50cfb) | 9,138,888.888889 | $14,607.40 |
| Sep 23 | [0x109d75…](https://etherscan.io/tx/0x109d7551495c45ee316fbfd5b03a464e108306838bdc0a4eada69bfb2127d0f1) | 10,000,000, alongside other legs and a debt repayment | $7,406.66 |

$363,376.66 of the unmatched-outflow cost comes from accounts first created by
September transactions; $6,431.05 comes from earlier transactions. The latter
is September financing cost on carried principal, not a historical restatement.

The combined daily cost of all other accounts is highest on September 23
($30,296.37), 24 ($30,217.82), and 25 ($30,002.16). Those daily numbers include
PSM and transit accounts as well as unmatched outflows.

**Next investigation:** resolve the RLUSD destination/return legs for the above
transactions, then apply the same normalization rule to the recurring pattern.
Do not treat these outflows as expenses, yield, or arbitrary venue capital.

## Custody and financing accounts

PSM gross costs comprise Arbitrum **$169,281.16**, Base **$10,219.45**, and Optimism
**$4,485.38**, plus smaller balances. These are explicit `psm:` accounts outside
the selected venue mapping. Their idle-USDS deduction must also be included;
adding their gross cost directly would overcharge them.

Savings-interest accounts retain borrowed basis after the replay marks the spent
cash to zero (`allocation_capital.py`, external `interest` operation). Their
costs are spUSDT Ethereum **$27,573.82**, spUSDC Ethereum **$18,020.23**,
spUSDC Avalanche **$4,810.17**, and spPYUSD Ethereum **$25.82**.

These are the model's retained financing balances under proportional source
attribution, **not proof of fresh Sky draws paying VSR interest**. In particular,
the expected business funding of VSR from investment earnings must not be
replaced with an assumption that these are live investment allocations. Review
this attribution separately before deciding how to present prime-level costs.

Unpriced selected accounts contribute only $69.46: Base/Optimism/Unichain June
pending custody (~$20 each), Binance OTC ($4.68), B2C2 ($3.21), and Paxos ($0.03).
Their exact account mapping is retained in the saved capture header.

## Why deductions offset $203,150.81

| Deduction group | Applied to priced allocations | Independent global deduction | Difference |
|---|---:|---:|---:|
| Lending idle, S1/S4 | $1,363,287.70 | $1,363,287.70 | $0.00 |
| AMM idle and global SDE (including PSM USDC) | $271,114.04 | $351,802.81 | −$80,688.76 |
| Raw ALM USDS, S38/S44 | $186,545.79 | $281,139.49 | −$94,593.70 |
| PSM idle USDS | $0.00 | $27,868.36 | −$27,868.36 |
| **Total** | **$1,820,947.54** | **$2,024,098.35** | **−$203,150.81** |

Global deductions use position balances. Conditional allocation deductions are
the difference between priced Sky principal and the saved conditional cost;
per-venue pricing floors chargeable principal at zero. Their coverage and basis
therefore need not match. The table isolates the differences but does not prove
whether each is missing custody mapping, funding attribution, or a floor effect.
No offset has been assigned to another venue to force agreement.

## Reproduction and limits

```sh
python scripts/audit_saved_funding_gap.py \
  --saved-dir /tmp/spark-september-financing-results \
  --control /tmp/september-allocation-inputs/spark/provenance.json \
  --output reconciliation/spark_saved_funding_gap_2026_09.json
```

The JSON includes input SHA-256 hashes, all daily stock-cost components, grouped
outflow shapes/origin months, deduction controls and the 30 largest non-selected
accounts with normalized transaction evidence where applicable. Raw replay
snapshots remain local and are required to reproduce it. Transaction references
are derived from the saved capture; no new explorer verification was performed.

Daily effective rate = `daily_sky_rev / utilized`. Residual = financing cost +
other-account cost + unpriced-selected cost + independent cash-debt difference
+ (priced gross cost − conditional allocation cost − global deductions).
The result is independently checked against the previously saved global gap.

**Recommended order:** resolve recurring RLUSD routes; map PSM custody and its
idle deduction; reconcile LP/raw-USDS funding versus deduction coverage; review
Savings-interest attribution; then clear the small transit/pending accounts.
Publication remains a separate decision after validating resulting costs.

## Follow-up: RLUSD destinations identified

The subsequent [RLUSD transfer audit](spark-september-rlusd-routes.md) identifies
Sentora and SparkLend receipt-token positions missing from the saved replay.
Verified deposit transactions are associated with $363,345.66 of the old residual
costs. This supersedes the destination uncertainty above; corrected funding-cost
calculation and publication remain pending.

The [refined residual analysis](spark-september-remaining-costs.md) separates
PSM USDC deductions from the earlier aggregate AMM/SDE category and identifies
the S62 zero-cost floor and raw-USDS funding-basis difference explicitly.
