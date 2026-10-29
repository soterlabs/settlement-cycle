# Nested fsUSDS capital movements: both sides must be in USD

Capital tracing inherited the report pricer's documented approximation that an
outer ERC4626 vault's sUSDS underlying is worth $1. However, direct bridged sUSDS
cash movements already used Ethereum sUSDS's exchange rate at the transaction
time. This made the two legs of an ordinary Fluid deposit or withdrawal disagree.

For example, Base transaction
[0x24d20b…368f](https://basescan.org/tx/0x24d20baac2d95fbce780bc56cd4a1730f863ac8d7898ad1137b196cc18ad368f)
deposited 10,000,000 sUSDS on February 12, 2025. The cash leg was
$10,370,717.19629494585, while the old fsUSDS leg was $10,000,000: a false
$370,717.19629494585 outflow. On April 12,
[0x684b12…6226](https://basescan.org/tx/0x684b121963ccd098ba5d6aa72eb4c7259d099fbf4ae0b3f025dd5bad00566226)
withdrew 5,000,000 sUSDS, worth $5,236,503.472661783995, producing a false
$236,503.472661783995 receipt under the old method.

`normalize/allocation_capital.py` now prices canonical sUSDS by address, using
its origin vault at the matching historical timestamp for bridge representations.
It converts the outer share mark and the ERC4626 event's underlying amounts into
USD. Async adapters already return USD and are not converted again. Exact cash
withdrawals still release the actual redeemed fraction of shares. This is a
capital-tracing correction only; `normalize/prices.py`, API revenue, settlement
reports, debt, and global borrowing costs are unchanged.

The frozen evidence contains all 19 S36 (Base) and three S42 (Arbitrum)
transactions in the inception-to-August diagnostic history. Each receipt has
one vault event and matching share and underlying token transfers at the ALM.
The repair checks the original isolated two-leg snapshot against that evidence,
then applies the already-observed sUSDS cash USD price to the fsUSDS mark and
movement. It does not infer a loan, recognize new income, or balance an unknown
payment. These transaction discrepancies are historical gross flows, not an
August revenue or settlement adjustment.

Reproduce the diagnostic input repair:

```sh
PYTHONPATH=src PYTHON_DOTENV_DISABLED=1 .venv/bin/python \
  scripts/repair_spark_fsusds_capital.py \
  --history /tmp/pr215-spark-exact-withdrawals-history.jsonl.gz \
  --evidence tests/fixtures/spark_fsusds_capital.json \
  --output /tmp/pr215-spark-fsusds-history.jsonl.gz \
  --audit /tmp/pr215-spark-fsusds-repair.json
```

The committed audit records every affected transaction, both input/output hashes,
and the false receipt/outflow totals. The next full diagnostic replay includes
this repair alongside the Base Morpho exact-cash correction. Until that replay
finishes, this establishes the individual exchange corrections, not a completed
Spark funding reconciliation.
