# Spark Savings-linked sUSDS funding origin

The funding-origin diagnostic now follows two separate legs: saver assets
(USDC/USDT/PYUSD) received by the ALM, and USDS drawn against Spark's ilk and
deposited into sUSDS. A saver deposit can trigger this operation without being
the original source of the USDS deposited into sUSDS. We classify the latter
as Sky borrowing only when transaction receipts prove that route.

This is original funding attribution, not a new calculation of outstanding
debt or borrowing costs. A later repayment does not erase the original funding
source. Normalized debt, Savings obligations, capital balances, settlements and
API revenue remain unchanged. Pre-2026 history supplies opening basis only.

## August-end result

| S32 sUSDS/POL acquisition capital | USD |
|---|---:|
| Total | 703,133,724.15 |
| Previously traced to Sky borrowing | 429,495,619.36 |
| Now traced to Sky borrowing | 617,587,857.46 |
| Additional attribution | 188,092,238.10 |
| Still unclassified | 85,545,866.69 |

Coverage is **87.83%** for this allocation. These are surviving acquisition
amounts after weighted-average withdrawal releases, not cumulative deposits,
current market value, or a Prime-wide reconciliation percentage.

## Evidence and safeguards

`scripts/audit_spark_ordered_draws.py` authenticates the configured Vault's
Draw/Wipe events, corresponding signed Vat frobs, actual USDS mint and
Buffer-to-ALM transfer, and the subsequent sUSDS deposit/share movements.
Draw less Wipe must match the already rate-normalized net debt change; it
does not assume Spark's cumulative rate equals one. The contract mechanics
are documented in the pinned [AllocatorVault source](https://github.com/sky-ecosystem/dss-allocator/blob/6e99d87374cff66e93fe44c55d4ed240dac1c3dc/src/AllocatorVault.sol).

The USDS prefix through the deposit must be isolated. Later receipts and
repayments are permitted and reconciled in log order to the normalized cash
movement. Opening unclassified USDS and forwarded shares conservatively reduce
the proven contribution. Savings Take events are checked against actual
underlying transfers and normalized external-funding draws; they are not
relabeled as Sky funding.

Of 126 cached candidate receipts, 107 qualify, including 40 with negative net
debt. For example, [this transaction](https://etherscan.io/tx/0x0d12032e7f15c191eeeac5f9ae65e197e935f2a4adba1439248a0423b8fa44ca)
draws and deposits 6,506,534.311290 USDS, then repays 6,506,538.474646 USDS.
Its net debt decreases by 4.163356 USDS, but the sUSDS deposit still has a
proven borrowed origin. The diagnostic replaces the net-draw attribution
with the proven contribution rather than adding a second copy.

Nineteen receipts remain rejected: thirteen have additional USDS cash before
the deposit, five lack the required authenticated draw, and one does not match
the required debt metadata. Rejection means this narrow proof is insufficient,
not that the entire transaction is excluded from the baseline audit.

The largest remaining surviving contributions are July 15 mixed cash
(11.094m), July 13 Optimism withdrawal (10.628m), and July 13 Base
bridge return (8.281m). Their upstream origins require separate evidence.
This change does not assume all sUSDS/POL is borrowed and does not classify
the underlying Savings-funded allocations automatically.

## Reproduction

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_spark_savings_origin.py \
  --history /tmp/pr215-spark-v1-history.jsonl.gz \
  --receipts tests/fixtures/spark_susds_ordered_draw_receipts.json.gz \
  --start 2026-01-01 --end 2026-08-31 \
  --output reconciliation/spark_susds_ordered_funding_origins_2026_08.json.gz
```

The artifact contains input/source hashes, accepted and rejected receipts,
transaction-level attribution, and a ranked residual that reconciles to the
closing unclassified capital. Tests use the committed normalized history and
receipt fixtures, including tampered evidence, negative net debt, opening
cash, forwarded shares, unchanged history and deterministic replay.
