# Spark USCC: identified subscription gap and unresolved cash attribution

The [October 16, 2025 spell](https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20251016/SparkEthereum_20251016.sol)
authorizes the exact USDC entrypoint `0xdb48ac0802f9a79145821a5430349caff6d676f7`
and enables USCC redemptions. Actual ALM transfers identify four payments,
including the onboarding test, totaling **150,010,000 USDC**. Three subsequent
issuer mints delivered **13,265,483.981402 USCC shares**. Each group completed
before the next began:

| Paid USDC | USCC shares received |
|---:|---:|
| 50,010,000 (10k test plus 50m) | 4,428,393.645144 |
| 50,000,000 | 4,424,373.911272 |
| 50,000,000 | 4,412,716.424986 |

These cash/share ratios are approximately $11.29–$11.33 per share, not $1.
`config/spark.yaml` S22 currently uses `const_one` with an old zero-holdings
comment. Replaying historical holdings at that price is not valid NAV evidence.
The issuer's [token implementation](https://github.com/superstateinc/ustb/blob/78e8ca22a319efd265e7d6ba2c326475cb6b6e2e/src/SuperstateToken.sol)
describes NAV per share and an oracle rather than par-stable token pricing.
Historical calls at all three issuance blocks and two redemption blocks return
zero for `superstateOracle()`; `getChainlinkPrice()` reverts. A historical feed
or issuer NAV evidence is needed. No $1 fallback or inferred constant purchase
price has been installed by this investigation.

Three later burns remove **all 13,265,483.981402 shares**, leaving zero token
units. They have no USDC return in the same transaction. Two nearby USDC
receipts from `0x55fe002aeff02f77364de339a1292923a15844b8` total
**151,013,951.26 USDC**:

- [100,682,211.13 USDC](https://etherscan.io/tx/0x6641633601e1b4c7364e7b5b497ebed27d76b31f6988edab1bb73b35b95c032c), block 23,926,724.
- [50,331,740.13 USDC](https://etherscan.io/tx/0xb230b52789a38848821b049b93f2bfed9c4d84dc2eb32e0d4cbb1ae69d3623fc), block 23,940,496.

They are **candidates, not attributed redemption proceeds**. A common payer,
compatible amounts and nearby dates do not independently prove the issuer or
order association. The 1,003,951.26 difference from subscriptions is therefore
not asserted or booked as USCC profit. No interior tracing of that payer is
needed; settlement/order evidence linking these payments to the three burns
would be sufficient.

`audit_spark_uscc_history.py` reproduces the inventory from canonical logs,
checks the three subscription groups and complete token exit, and keeps
candidate cash explicitly unassigned. The fixture includes the pinned oracle
call results. The resulting artifact is
`reconciliation/spark_uscc_history_inventory_2026_08.json`.

This checkpoint identifies why generic share-price and transfer matching are
insufficient here. It changes no production pricing, settlement, revenue,
capital basis or global costs. A complete tracing fix needs historical NAV and
cash-return association; this inventory is not a reconciliation claim.
