# Grove

Grove (formerly "Bloom") is a prime agent in the Sky ecosystem that mints USDS debt against ilk `ALLOCATOR-BLOOM-A` and deploys it across a diversified, multi-chain RWA + on-chain-credit book (~$3.4B cumulative debt as of Apr 21, 2026).

Unlike OBEX (single venue: Maple syrupUSDC), Grove spans ~28 venues on Ethereum, Base, Plume, Monad, and Avalanche. Phase 1 implementation covers Ethereum only.

See [PRD.md](PRD.md) for the full design and [QUESTIONS.md](QUESTIONS.md) for open items.

## Key contracts

| Contract | Address | Role |
|----------|---------|------|
| ALM Proxy | `0x491edfb0b8b608044e227225c715981a30f3a44e` | Holds all allocation positions |
| Subproxy | `0x1369f7b2b38c76B6478c0f0E66D94923421891Ba` | Holds idle USDS / sUSDS (earns agent rate) |
| AllocatorVault | `0x26512a41c8406800f21094a7a7a0f980f6e25d43` | Calls `frob` on the Vat |
| AllocatorBuffer | `0x629ad4d779f46b8a1491d3f76f7e97cb04d8b1cd` | Intermediate USDS buffer |
| MainnetController | `0xB111E07c8B939b0Fe701710b365305F7F23B0edd` | Relayer / rate-limit policy |

## Ilk

- **Name:** `ALLOCATOR-BLOOM-A`
- **bytes32:** `0x414c4c4f4341544f522d424c4f4f4d2d41000000000000000000000000000000`
- **First frob:** 2025-05-14 (100K test, unwound 2025-06-25)
- **Real ramp:** 2025-07-24

## Dune query parameters

| Parameter | Value |
|-----------|-------|
| `ilk_bytes32` | `0x414c4c4f4341544f522d424c4f4f4d2d41000000000000000000000000000000` |
| `subproxy_address` | `0x1369f7b2b38c76B6478c0f0E66D94923421891Ba` |
| `alm_proxy_address` | `0x491edfb0b8b608044e227225c715981a30f3a44e` |
| `start_date` | `2025-05-01` |
| `calendar_start_date` | `2025-05-14` |

Because Grove predates the SSR boundaries encoded in the shared queries (which assume Nov 2025+), Grove-specific copies extend the `CASE` back to 2025-03-24. The full SSR history lives in [`RULES.md`](../RULES.md).

## Allocation venues (Ethereum — Phase 1)

See [PRD.md §4.1](PRD.md#41-ethereum-18) for the full table with pricing strategy per venue. Summary:

- 3 Aave aTokens (E1–E3)
- 3 Morpho ERC-4626 vaults (E4–E6)
- 4 RWA tokens priced at `$1.00` placeholder pending NAV getter investigation (E7–E10)
- 1 Curve stableswap LP, AUSD/USDC (E11)
- 1 Uniswap V3 position, AUSD/USDC (E12)
- 6 raw ERC-20 holdings (E13–E18)

Base/Plume/Monad/Avalanche venues are out of scope for Phase 1 — see [QUESTIONS.md](QUESTIONS.md) Q4.

## Balances snapshot (2026-04-21)

Subproxy `0x1369f7...91Ba`:
- USDS: 22,680,104
- sUSDS: 0.009 (dust from 2026-04-14)
- USDC: 753,649 (does not earn agent rate — flagged for reconciliation)

ALM Proxy `0x491edfb...f3a44e` (idle, expected to be ~0 since positions are in allocations):
- USDS: 0
- USDC: 0
- AUSD: ~3
- RLUSD: ~113M

## PnL summary

Pending — will be populated once `grove_monthly_pnl.sql` is built.
