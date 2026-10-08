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
zero for `superstateOracle()`; `getChainlinkPrice()` reverts. The follow-up below
identifies a separate historical NAV feed. No $1 fallback or inferred constant
purchase price has been installed by this investigation.

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
capital basis or global costs. A complete tracing fix still needs cash-return
association and integration of validated historical prices; this inventory is
not a reconciliation claim.

## Historical NAV evidence found in a separate feed

Chainlink's [USCC NAV feed](https://data.chain.link/feeds/ethereum/mainnet/uscc-nav-per-share)
is `0xAfFd8F5578E8590665de561bdE9E7BAdb99300d9`. This is separate from
the token's unset internal oracle. Pinned calls return `description() = USCC NAV`,
six decimals and valid `latestRoundData()` at every issuance/burn block:

| Block | Event | Published NAV | Quote age (seconds) |
|---|---|---:|---:|
| 23,626,140 | First issuance | 11.296558 | 82,056 |
| 23,633,261 | Second issuance | 11.293034 | 86,244 |
| 23,733,591 | Third issuance | 11.342953 | 86,244 |
| 23,919,053 | First burn | 11.369271 | 67,320 |
| 23,919,081 | Second burn | 11.369271 | 67,668 |
| 23,933,973 | Final burn | 11.380015 | 6,660 |

All are within the directory's 95,400-second heartbeat. A separate August
cutoff check returns 11.715712 with age 38,844 seconds. The raw ABI responses,
block timestamps and directory record are in `tests/fixtures/spark_uscc_nav.json`.
Tests reject stale, negative or zero answers, wrong feed identity and wrong
decimals; the audit never silently substitutes par.

Published NAV at token delivery is not necessarily the subscription's executed
price. For example, first issuance shares times that block's NAV equal
50,025,605.659201, whereas actual cash paid was 50,010,000. The second issuance
has the opposite difference: NAV value 49,964,605.008708 versus 50m paid.
These differences do not change borrowed acquisition basis, which must follow
actual subscription cash. Nor does a NAV quote authenticate the purpose of a
later transfer from a common payment wallet. Both candidate receipts remain
explicitly unassigned in the updated inventory.

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_spark_uscc_history.py \
  --fixture tests/fixtures/spark_uscc_history.json \
  --nav-fixture tests/fixtures/spark_uscc_nav.json \
  --output /tmp/spark-uscc-inventory.json
```

This resolves the missing historical price-source evidence. It does not change
S22's production configuration, repair the replay's USCC marks, or claim that
the 151m of candidate cash has been traced to its redemptions.
