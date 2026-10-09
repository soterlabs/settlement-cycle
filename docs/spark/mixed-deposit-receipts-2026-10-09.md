# Deposits must not erase other share receipts

The capital normalizer used an ERC-4626 Deposit's actual cash amount to replace
the whole transaction's positive share movement. This correctly handles deposit
rounding, but erased unrelated incoming shares in the same transaction.

A complete sUSDS transfer/deposit inventory through Ethereum block 25,878,704
identifies 176 affected transactions. The pinned `convertToAssets(1e18)` reads
and raw event witnesses are in
`tests/fixtures/spark_susds_mixed_deposits.json.gz`.

The fix retains cash acquisition cost for deposited shares and execution-time
NAV for additional received shares, without counting performance-fee shares
twice. Transactions with outgoing shares continue through the existing path.
This changes capital tracing, not published revenue or Sky debt.

## Concrete proof

[April 29 transaction](https://etherscan.io/tx/0xbf6382cb7c44bb47b75366e1d7ed5bc1959af028521a83a444cd6c4aae268683):

- Deposit: 9,924,803.542007663477553834 USDS; 9,065,115.295724932709055260 sUSDS minted.
- Separate purchase: 861,111.111111 USDT paid to `0x00836fe54625be242bcfa286207795405ca4fd10` in log 702.
- 786,508.075268232816886005 additional sUSDS received from that address in log 703.
- Historical `convertToAssets(1e18)` returns 1,094,834,783,479,053,581 raw USDS.

The old movement recorded only the deposit. The additional shares have a NAV of
861,096.398291 USD, restoring that amount to the allocation movement. The real
14.712820 USD execution/NAV shortfall remains outside the share value. It is not
unobserved capital or a balancing gain. The full receipt is independently saved
in `tests/fixtures/spark_susds_mixed_april29.json`.

Across all 176 cases, the repaired share value is **49,595,481.191452 USD**.
This is historical transaction volume, not monthly revenue or borrowing cost.
`reconciliation/spark_mixed_deposits_2026_08.json` identifies every transaction,
old/new movement and evidence hashes. Its input is the explicitly patched
Savings diagnostic history, preserving those provisional funding assumptions.

## Validation and remaining work

All 176 witnesses reproduce the old overwrite, agree on deposit mint amounts,
and have only incoming share transfers. Regression tests verify unchanged debt,
all unrelated movements, rejection of incomplete mint evidence, and prevention
of a second repair. A normalizer integration test also covers a deposit and an
independent peer purchase in the same execution. The focused suite passed 29
tests. The full funding replay will measure the allocation-cost effect separately.

```
PYTHONPATH=src .venv/bin/python scripts/repair_spark_mixed_deposits.py \
  --history /tmp/pr215-spark-savings-history.jsonl.gz \
  --evidence tests/fixtures/spark_susds_mixed_deposits.json.gz \
  --output /tmp/pr215-spark-savings-mixed-history.jsonl.gz \
  --audit /tmp/pr215-spark-mixed-deposit-repair.json
```
