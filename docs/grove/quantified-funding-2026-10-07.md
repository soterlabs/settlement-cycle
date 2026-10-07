# Grove funding uncertainty and Plume JTRSY custody

PR #215 follow-up to [EOA boundaries](eoa-boundaries-2026-10-07.md).
The August 2026 diagnostic was replayed from immutable normalized inputs after
adding funding envelopes and the October 2025 Ethereum→Plume JTRSY route.
Published settlements, global borrowing costs and API revenue data are unchanged.

## Result

| Metric | Before | After |
|---|---:|---:|
| Unmatched receipts | 242 | 239 |
| Unmatched outflows | 468 | 465 |
| E22 Apollo average modeled borrowed principal | $0.00 | $16,728,886.49 |
| E22 Apollo modeled borrowing costs | $0.00 | $51,868.61 |
| Fully eligible allocation borrowing-cost subtotal | $1,233.51 | $1,233.51 |

The E22 change is provisional attribution, not a newly validated charge or a
settlement adjustment. Earlier unresolved funding still propagates through its
history. Neither ilk fully reconciles:

| Ilk | Eligible allocation costs | Global costs excluding MSC | Unreconciled gap |
|---|---:|---:|---:|
| ALLOCATOR-BLOOM-A | $0.00 | $3,448,941.99 | $3,448,941.99 |
| ALLOCATOR-GROVE-A | $1,233.51 | $11,784.74 | $10,551.22 |

The evidence, input/code hashes, before/after allocation rows and bounds are in
[grove_quantified_funding_2026_08.json](../../reconciliation/grove_quantified_funding_2026_08.json).
Do not sum signed modeled costs and call that explained borrowing costs: missing
basis alongside full known SDE deductions can produce negative diagnostic values.

## Quantitative uncertainty

`validate_allocation_financing.py --quantify-uncertainty` adds a separate,
opt-in diagnostic. Existing settlement calculations, validated subtotals, net
PnL and net APY remain unchanged. It introduces no publication gate or warning.

- Observed draws create borrowed basis; known income never does.
- Transfers carry weighted-average basis and its interval by original ilk.
- Unknown cash can carry at most its face value in borrowed capital. Unknown
  custody shares can have historical basis above NAV, so their upper limit is
  observed outstanding debt, not current value.
- Unknown receipts can reattribute already-recorded capital. Possible source
  accounts lose certainty; the receipt never creates another draw.
- Repayment intervals account for reductions of basis elsewhere, including
  refinancing across ilks. Realization clips principal at actual cash proceeds.
- Aggregate allocation bounds apply the shared outstanding-debt cap. Individual
  venue maxima are mutually dependent and must not simply be added.
- The per-ilk validator uses verified deduction ownership when available; it
  does not assign BLOOM's SDE deduction to GROVE because funding origins are
  uncertain. Comparisons allow one cent, like the existing reconciliation.

A regression with $100m of principal and a $0.44 unknown cash repayment leaves
less than $0.45 of uncertainty in the remaining position. It does not disqualify
$100m numerically. The binary provenance flag is retained separately.

These are conservative outer bounds, not confidence intervals or proof of
funding routes. They assume complete observed draws/repayments and correct
normalized movements, pricing and custody matches. They do not cover missing
borrowing or incorrect input data. Unmapped/unsupported allocations are listed
separately. A complete account of capital is still required for reconciliation.

The current broad intervals, using verified deduction owners, are:

| Ilk | Signed diagnostic cost lower bound | Upper bound |
|---|---:|---:|
| ALLOCATOR-BLOOM-A | −$4,837,640.77 | $3,448,941.99 |
| ALLOCATOR-GROVE-A | $1,501.50 | $11,784.74 |

Both targets are within their interval to a cent. That does **not** establish
reconciliation: a debt-capped upper bound can coincide with the control by
construction. BLOOM's negative lower bound reflects applying known deductions
to a very low possible traced basis, not a payable credit. Larger unknown
custody receipts keep the intervals broad despite the small-cash improvement.

## Exact JTRSY route

The [October 2, 2025 Ethereum spell](https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20251002/GroveEthereum_20251002.sol)
sets Centrifuge destination domain 4 and Grove's Plume ALM recipient. Its
[Plume companion](https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20251002/GrovePlume_20251002.sol)
enables JTRSY redemptions and Apollo ACRDX subscriptions.

The missing path was JTRSY shares sent from Ethereum, minted on Plume, placed
in redemption escrow, redeemed for USDC, then used for Apollo. JTRSY's temporary
Plume holding is absent from the configured report venues; the replay saw the
cash proceeds without their original funding.

| Ethereum transfer | Exact Plume message delivery | Raw JTRSY units |
|---|---|---:|
| [October 13](https://etherscan.io/tx/0x8fd8753896fb1241c3c2bf772f8dafa24a37b5680150d30d5df765220b1fc012) | `0xca2048e3c4440d4709db3c805ce973aa84e6135a8f353f0d09643fad4626ae00` | 18,494,562,293,919 |
| [October 14](https://etherscan.io/tx/0x62994a29311c71f6b668a3572ca1684124ee6e0ca408157e9c8d9030a2379362) | `0x715709306c73fd2146b6ce83e2dcbd67f13b4a947ab4f46121dd5ac95a43533e` | 18,487,141,730,747 |
| [October 15](https://etherscan.io/tx/0xb6536bca914b8403fbbe473fd1d7c4982041da0a2674f630a78f90dab12ae72a) | `0xdab9f27c19fb429d3bad7d53930cdfa056acd41ecd83cf13ab2b020988eb9b57` | 9,242,801,936,921 |

The three USDC claims were $20,008,042.283908, $19,981,663.966797 and
$10,020,691.280307: **$50,010,397.531012 total**. The request/escrow burns exhaust
all 46,224,505,961,587 raw shares. The ERC7540 `Withdraw` event reports one extra
raw share per claim due to rounding; actual request/burn units govern allocation
of basis. The second and third claims are not one-to-one with the second and
third bridge deliveries: track the pooled share holding, not equal USD amounts.

`grove_plume_capital.py` keeps a beneficial-custody account until each claim,
using actual redeemed share fractions. It isolates the Ethereum custody leg
from the unrelated JAAA mint/draw in October 15's multicall. A pinned history
ending before redemption retains pending basis. Missing source/delivery,
changed cash amounts and partially appended adapted histories are rejected.

Canonical event evidence is pinned in
[grove_plume_jtrsy_events.json](../../tests/fixtures/grove_plume_jtrsy_events.json).
Tests compare exact source/destination message payloads, token transfers,
requests, burns and USDC claims. They also cover custody preservation, separate
same-transaction funding, idempotence and incomplete-history rejection.

## Validation and remaining work

- Broad unit/monthly integration suite: 1,446 passed, two optional-dependency/
  isolated-Postgres skips, seven pre-existing fixture warnings.
- Final focused uncertainty, Plume and per-ilk reconciliation suite: 13 passed.
- Ruff and diff whitespace checks passed.
- August Spark and Grove provenance hashes remain unchanged.

The next material evidence to resolve is the remaining $50m Ethereum receipt
routes, starting with `0x2f544c36733dfc9c71c76a371e21a3c5d04b3af475635a85d7000996c74c0ae4`
and `0x8f8e781c2fbfdefdee08a225e702b319e9fabd65c3c0a1167df5e13f0303dc8e`.
No source has been invented for these receipts, and no residual is plugged into
a venue to make the totals match. Spark was not replayed in this follow-up.
