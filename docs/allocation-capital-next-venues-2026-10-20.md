# JAAA and Base vault funding origins

Continues PR #215's allocation-local audit. Saved histories end August 31,
2026. Pre-2026 activity establishes opening cost only. No revenue, API,
settlement, or production borrowing-cost calculation is changed.

## Additional allocation capital mapped

| Allocation | January 1 capital | 2026 contributions | Cost basis released | August 31 capital |
|---|---:|---:|---:|---:|
| Grove JAAA, Ethereum + Avalanche (E8/E20) | 990,000,000.00 | 0.00 | 614,450,341.91 | **375,549,658.09** |
| Grove Steakhouse Prime, Base (E23) | 0.00 | 222,700,000.00 | 20,004,681.25 | **202,695,318.75** |
| Spark Morpho USDC, Base (S34) | 167,562,617.67 | 1,105,768,521.64 | 990,215,000.05 | **283,116,139.25** |

Amounts use the existing weighted-average acquisition-cost convention. Gross
contributions include turnover, not only new borrowing. Revenue classifications,
normalized execution values and earlier reviewed adapters remain inputs.

Adding these non-overlapping positions to the previous audit gives:

| Prime | Capital attributed to audited allocations | August-end total ilk debt | Size relative to debt |
|---|---:|---:|---:|
| Spark | **1,876,811,705.64** | 2,943,862,416.41 | **63.75%** |
| Grove | **2,378,023,491.66** | 2,701,642,082.31 | **88.02%** |

These are scope/progress ratios, **not certified percentages of Sky funding
origin explained**, nor percentages of total allocated capital. Allocation
capital may include reinvested gains or external funding; debt includes
non-investment financing entries. Do not charge the entire mapped amount CoF.

## JAAA: carry acquisition cost across chains

Auditing E20 alone treats five bridged receipts as 251,235,738.71 of new
contributions at arrival NAV. The grouped E8/E20 audit instead carries
**250,283,019.05** of acquisition cost to Avalanche: a **952,719.66** difference.
There are no E20 movements during January–August 2026. The remaining Ethereum
capital, including negligible pending dust, is **125,266,639.04**.

The existing `grove_historical_capital.py` links all five executions through
explicit Ethereum/Avalanche transaction pairs, guarded source/destination
blocks and movement shapes. The source-side in-transit accounts remain owned
by E8 until fulfillment. Auditing both venues together keeps those transfers
inside one allocation and prevents both a second contribution and capitalization
of the NAV increase between send and receipt.

The precise authorization is the
[August 21, 2025 Grove spell](https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20250821/GroveEthereum_20250821.sol).
The previously reviewed raw proofs are
`tests/fixtures/grove_initial_custody_events.json`; no new live RPC validation
is claimed here. First source execution:
[Ethereum transfer](https://etherscan.io/tx/0xc49e1b375beae51df861da3f19da036223f0381f534909adffac3fe7a40f29f2).

Grouped audits reject overlapping account ownership. January 1 checkpoints
reproduce every daily total using only 2026 movements; tests also check that a
post-bridge withdrawal releases original cost, not arrival NAV.

## Grove Base: bridge receipt to cash to vault

The bounded cash-source audit follows authenticated CCTP claims into an empty
Base ALM USDC balance, then requires isolated vault deposits. It retains a
receipt across split deposits only while all intervening spending goes to the
same vault; unrelated spending or mixed receipts invalidate that shortcut.
Amounts are not matched solely by proximity or equality.

- **45 deposits / 217,700,000 USDC (97.75% of gross contributions)** have this
  exact bridge-to-cash-to-vault path.
- The March 27 receipt of 10m funds separate 1m and 9m deposits. The receipt is
  counted once; artifact bridge-source amounts describe the whole receipt,
  while only each `contribution` enters totals.
- The remaining **5m** April 10 deposit combines **3,994,379.874274** newly
  bridged USDC with **1,005,620.125726** redeemed from this same vault earlier
  that day. Its immediate cash origin is also explained, under a separate
  bounded three-transaction check. Thus all 46 deposits have identified local
  routes; 97.75% follow the simpler empty-cash bridge route.
- That [redemption](https://basescan.org/tx/0x2cbf65daeca836c3b1f4354c9e06f6f5790a98a4c4232ccfc14e5bfbfd840ce1)
  releases **1,004,134.51** of acquisition cost and **1,485.61** of gains.
  Reinvesting the cash does not make the gains Sky-borrowed principal.
- All **222.7m** contributions enter the allocation-local ledger; three
  withdrawals release **20,004,681.25** of cost, leaving **202,695,318.75**.

First complete example:

- [Base bridge receipt](https://basescan.org/tx/0x919790807811b5bdfe20264e8d6341fe256b5941a8250de2aa2a5a4ca62b9050)
- [10m vault deposit](https://basescan.org/tx/0x57c46ed7362980e9055c8b1d3b1d69cdd237c87b6222c2cd3d6b2711902f4508)

The artifact retains the authenticated message identifier, Ethereum source
transaction and its draw candidates. A source transaction containing a draw
does not prove that every bridged dollar came from that draw when other funds
are present. This step proves the route, not a mixed-source allocation policy.

## Spark Base

The S34 ledger excludes **114,516.63** of explicitly classified 2026 receipts
as income and does not capitalize passive share appreciation. Its opening
checkpoint reproduces all 244 daily closing balances without pre-2026 replay.

Large contribution examples retain CCTP claim candidates:

- August 12: **69,578,775.96**,
  [transaction](https://basescan.org/tx/0x818d68f1ec509de07f305e8f6d03194ed80233c24683fc7beb3111c7bf242326).
- April 19: **33,966,627.92**,
  [transaction](https://basescan.org/tx/0x0c6db7130a13580cac6f5b9124c2dfb7ea9155bdee72a1c2b1d7cef0873a2e87).

These transactions contain multiple source claims. This audit does not promote
all of those source candidates into a unique Sky-borrowed funding attribution.
The Grove empty-cash shortcut has not been claimed for Spark.

## Reproduction

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_allocation_positions.py \
  --history /tmp/pr215-grove-eoa/history.jsonl.gz --venues E8 E20 --group \
  --start 2026-01-01 --end 2026-08-31 \
  --output reconciliation/grove_jaaa_allocation_positions_2026_08.json.gz
PYTHONPATH=src .venv/bin/python scripts/audit_allocation_positions.py \
  --history /tmp/pr215-grove-eoa/history.jsonl.gz --venues E23 \
  --start 2026-01-01 --end 2026-08-31 \
  --output reconciliation/grove_base_allocation_positions_2026_08.json.gz
PYTHONPATH=src .venv/bin/python scripts/audit_allocation_positions.py \
  --history /tmp/pr215-spark-v1-history.jsonl.gz --venues S34 \
  --start 2026-01-01 --end 2026-08-31 \
  --output reconciliation/spark_base_allocation_positions_2026_08.json.gz
PYTHONPATH=src .venv/bin/python scripts/audit_allocation_cash_sources.py \
  --history /tmp/pr215-grove-eoa/history.jsonl.gz --venue E23 --cash-venue E27 \
  --start 2026-01-01 --end 2026-08-31 \
  --output reconciliation/grove_steakhouse_cash_sources_2026_08.json
```

The archives include input/source hashes and transaction-level evidence.
Validation: 105 tests covering allocation ledgers, bounded cash routes, existing
JAAA links and CCTP links pass; Ruff passes on the changed Python files.

Next: follow selected large Spark Base CCTP claims and Grove's authenticated
bridge sources to their Ethereum cash/draw sources. Preserve
the distinction between tracing a path and proving which funding source
financed it.

Grove follow-up: [Steakhouse funding-origin audit](grove/steakhouse-funding-origin-2026-10-20.md)
now identifies original Sky borrowing for 95.56% of E23's closing capital,
separates reinvested gains, and narrows the unresolved portion to six source
transactions. This does not change the aggregate coverage ratios above.
