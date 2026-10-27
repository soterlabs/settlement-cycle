# Funding-origin priorities and Spark sUSDS receipt evidence

PR #215 research through August 31, 2026. This compares the large allocations
already audited, not every allocation in either Prime. The normalized histories
are the existing reviewed snapshots; six new Ethereum receipts were checked
against them. No production accounting, API, settlement or CoF logic changes.

## Ranked remaining capital

USD millions, August-end acquisition capital. “Remaining” includes possible
external funding and reinvested gains: it is not necessarily untraced Sky debt.
Borrowing attribution uses the conservative direct-draw and weighted-average
origin convention documented in `audit_scoped_borrowing_origins.py`.

| Prime / allocation | Capital | Traced original borrowing | Remaining to classify |
|---|---:|---:|---:|
| Spark S34 — Base Morpho USDC | 283.116 | 0.000 | **283.116** |
| Spark S32 — sUSDS / POL | 703.134 | 429.496 | **273.638** |
| Spark S1 — spUSDS | 681.733 | 584.610 | **97.122** |
| Grove E9 — JTRSY | 841.109 | 756.724 | **84.385** |
| Grove E23 — Base Steakhouse Prime | 202.695 | 202.316 | **0.178 unresolved**, plus 0.201 identified gains |
| Grove E10 — BUIDL | 654.670 | 654.610 | **0.060** |
| Grove E42 — Galaxy Warehouse | 304.000 | 303.957 | **0.043** |
| Grove E8/E20 — JAAA across Ethereum/Avalanche | 375.550 | 375.550 | **0.000** |

E23 uses its separately validated upstream cash-origin audit. The other rows
use the new coverage tool. JAAA must be grouped across both chains; arrival NAV
is not new contributed capital. S34's zero means the *direct same-transaction*
method has not crossed its bridge/cash boundary; it does not imply zero Sky
funding. The previously identified CCTP paths remain the next evidence to use.

Anchorage retains its separate facility-principal audit: 208.829m principal,
148.275m traced direct origin, and 60.555m not proved there. The generic
vault-disposal convention gives a different result for this facility and is
not substituted for its reviewed principal-return policy. These differing
policies are not silently combined into a certified Prime-wide percentage.

## Six isolated Spark routes

The broad normalized transactions mix Sky draws and Savings funding. Receipts
show a narrower USDS path: the sole USDS ingress is the configured Spark Buffer,
followed by the deposit into sUSDS. Savings cash arrives in another token.

| Date | Transaction | USDS paid into sUSDS |
|---|---|---:|
| April 30 | [0xe8ae…434f](https://etherscan.io/tx/0xe8ae492d7641accdc12d18c55bf22706682807cf9311856b51f351bf0bdc434f) | 496,553,401.881669 |
| May 1 | [0x9cea…51ed](https://etherscan.io/tx/0x9cea9ad16aec8e011097820067090e2416ee230fc6d1d3a3e021aa4bd6e051ed) | 288,043,618.600032 |
| May 9 | [0x1126…ce62](https://etherscan.io/tx/0x11264ba7a19e1f8d3e568bbc080698511ca7f8f0416a68a395126d88f82ece62) | 500,000,000.000000 |
| May 22 | [0x838d…1f38](https://etherscan.io/tx/0x838d77773ae08a229f10ccdbd44475a6cf53262f9e2e57091405f6fd9ae71f38) | 431,351,463.579229 |
| July 16 | [0x4fb0…a6fe](https://etherscan.io/tx/0x4fb006591e1dc4b4a249d8c9444c2951fa11065155715e35372c12fd7765a6fe) | 43,928,744.107644 |
| August 22 | [0xd981…7326](https://etherscan.io/tx/0xd98166dce8aff442ba6c00f88a78a2dac2bea2822deae0dc7fa7220fe2cf7326) | 9,999,776.319922 |

`audit_spark_draw_routes.py` checks successful transaction/block identity,
unique logs, token addresses, sender/recipient, order, the ERC-4626 Deposit,
and minted sUSDS shares. It matches the Buffer transfer to normalized ilk debt
within 1e-18 USD, allowing Vat rounding only. It then separates the witnessed
USDS movements for this diagnostic, preserving every movement, total debt and
external-funding operation. The rest of the transaction remains unclassified.

Two details prevent overstatement:

- May 1 spends 0.000000946094025255 of pre-existing USDS. That dust is not
  labeled a fresh Sky draw.
- May 9 forwards 126,734.429508902870621601 sUSDS shares to
  `0x00836fe54625be242bcfa286207795405ca4fd10` after minting. All forwarded value
  is charged against new borrowing first. Only the net 499,861,110.786349 of
  normalized S32 capital is eligible here; its downstream allocation is not
  also claimed. This explains the difference from the 500m cash payment.

After subsequent withdrawals, these six links increase S32's closing traced
borrowing from **405,963,203.49 to 429,495,619.36**, an increase of
**23,532,415.88** (computed before rounding). Its total acquisition capital and
all daily capital balances are unchanged. Funding-origin coverage rises to
**61.0831% of this allocation**, not of Spark's debt.

## Prioritize capital that remains, not gross deposits

The tool now ranks unresolved contributions by their remaining closing cost,
using the same weighted-average releases as the local ledger. Their sum must
reconcile to the single-account allocation's remaining unclassified capital.
The first four enormous April/May routes explain only 1.956m at August-end:
most of their capital was withdrawn. July 16 and August 22 explain 21.577m.

The next largest S32 cases include:

| Date | Closing residual from contribution | Specific next question |
|---|---:|---|
| July 17 | 18,705,125.13 | [Draw/deposit followed by USDS receipt and Buffer repayment](https://etherscan.io/tx/0x0b1906522d76ba1778c59c36d26b600f2923acc6c9c1bfcf52818c1c09067c7f). Gross draw is 55,844,407.696565; later USDS receipt/repayment is 50,204.059834. Preserve original funding without treating net batch debt as the gross draw. |
| August 16 | 16,180,127.87 | [Draw/deposit plus later USDS receipt and repayment](https://etherscan.io/tx/0x58f4bb9f462b15342a7fb56e6094da390af334b4c447ff8c875f847f1d71c0a4). Separate the 19,999,664.065726 deposit from the later 249,755.692626 receipt and 249,752.744783 repayment. |
| July 16 | 12,491,252.09 | [Additional mixed USDS route](https://etherscan.io/tx/0x3c682337950d93d1ac80b31f53a04bdee82f9d508e0612b5a64b1091ddfca3bb), currently rejected by the sole-ingress rule. |

Receipts for the first two retained cases are saved and tested to fail the
narrow isolated-route validator. They are not claims that attribution is
impossible. Extending the evidence to ordered gross draw/repayment legs is a
separate next step. At allocation level, Base Morpho is now the largest gap
among these audited positions, ahead of S32.

## Reproduction

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_allocation_origin_coverage.py \
  --history /tmp/pr215-grove-eoa/history.jsonl.gz \
  --groups E9 E10 E8,E20 E42 --start 2026-01-01 --end 2026-08-31 \
  --output reconciliation/grove_large_allocation_origin_coverage_2026_08.json.gz
PYTHONPATH=src .venv/bin/python scripts/audit_allocation_origin_coverage.py \
  --history /tmp/pr215-spark-v1-history.jsonl.gz \
  --groups S1 S32 S34 --start 2026-01-01 --end 2026-08-31 \
  --spark-draw-receipts tests/fixtures/spark_susds_draw_route_receipts.json.gz \
  --output reconciliation/spark_large_allocation_origin_coverage_2026_08.json.gz
```

Artifacts retain input/code hashes, account-level draw attribution and source
survival rankings where supported. S32's deterministic regression fixture is
already normalized/adapted and is loaded without reapplying adapters. Tests
verify receipt tampering rejection, custody overlap rejection, conservation of
debt/movements, capital balances unchanged, and gross-versus-surviving ranking.

## October 23 follow-up: Base Morpho

The [Base Morpho origin audit](spark/base-morpho-funding-origin-2026-10-23.md)
supersedes S34's zero-attribution row above: **264.632m (93.47%)** of its
August-end acquisition capital is now traced to Sky borrowing, leaving
**18.485m** to classify. It follows authenticated bridge claims and applies a
conservative cash-flow bound to mixed external-funding transactions. The other
rows are unchanged; sUSDS/POL is now the largest residual among these positions.

## October 26 follow-up: Savings-linked sUSDS draws

The [ordered sUSDS funding-origin audit](spark/savings-linked-susds-origin-2026-10-26.md)
supersedes S32's earlier result: **617.588m (87.83%)** of August-end acquisition
capital is traced to Sky borrowing, leaving **85.546m** to classify. It separates
Savings asset receipts from authenticated USDS draws, including draws followed
by repayments in the same transaction. Other allocation rows are unchanged.

## October 27 follow-up: three upstream sUSDS contributions

The [upstream origin audit](spark/susds-upstream-origins-2026-10-27.md) raises
S32's traced August-end capital to **636.767m (90.56%)**, leaving **66.366m**.
It proves the borrowed portion of July 15's mixed deposit and carries surviving
L2 borrowed basis through the July 13 Base and Optimism sUSDS returns. The
earlier “operations/Uniswap” label referred to Optimism and has been corrected.
Other allocations are unchanged.
