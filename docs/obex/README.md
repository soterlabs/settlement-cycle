# OBEX — Maple syrupUSDC Allocation

OBEX is a prime agent in the Sky ecosystem that mints USDS debt via the allocator framework and deploys it into Maple Finance's syrupUSDC product.

## Key contracts

| Contract | Address | Role |
|----------|---------|------|
| ALM Proxy | `0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2` | Holds syrupUSDC position, routes USDS/USDC |
| Subproxy | `0x8be042581f581E3620e29F213EA8b94afA1C8071` | Holds idle USDS (earns agent rate) |
| syrupUSDC (Maple) | `0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b` | Yield-bearing vault token |

## Ilk

- **Name:** `ALLOCATOR-OBEX-A`
- **bytes32:** `0x414c4c4f4341544f522d4f4245582d4100000000000000000000000000000000`
- **Auto Line config:** 2.5B ceiling, 50M gap, 86,400s TTL

## Governance spell (Nov 17 2025)

Transaction: [`0x865f7bbd...`](https://etherscan.io/tx/0x865f7bbd3976013411cb77c68822b362f50ea114c798257c1a041352e6ffc518)

This spell initialized the ALLOCATOR-OBEX-A ilk and configured the Auto Line. It also minted 21M USDS via `suck` (unbacked system debt through the Vow), **not** via `frob` against the ilk. The 21M USDS landed in the subproxy and has remained there since.

## Debt minting flow

Proper ilk-based debt minting started the day after the spell (Nov 18 2025) via `frob` calls:

1. AllocatorVault calls `frob` on the Vat for ilk ALLOCATOR-OBEX-A (dart > 0, dink = 0)
2. Internal DAI is created, converted to USDS
3. USDS flows to the ALM proxy
4. ALM proxy converts USDS -> DAI -> PSM -> USDC
5. ALM proxy deposits USDC into Maple syrupUSDC vault, receiving syrupUSDC tokens

By Jan 6 2026, 600M cumulative debt was minted across 25 frob transactions (typically 20-50M per day, respecting the Auto Line gap/TTL).

## Idle balances

- **Subproxy** holds ~21M USDS (from the initial `suck` mint). No sUSDS has ever been held.
- **ALM proxy** USDS balance is always 0 — it passes through immediately.
- The ALM proxy holds ~525.7M syrupUSDC (from 600M USDC deposited).

## Accounting rules

| Contract | Balance | Sign | Rate | PnL component | Comment |
|----------|---------|------|------|---------------|---------|
| Vat | Cumulative `frob` debt (ilk `art`) | - | SSR + 0.30% APY | Sky revenue | Interest paid to Sky |
| Subproxy | USDS balance | + | SSR + 0.20% APY | Agent rate | Demand-side earnings |
| Subproxy | sUSDS balance | + | 0.20% APY | Agent rate | Flat rate (not SSR-based) |
| ALM Proxy | USDS balance | Nets to 0 | N/A | — | Should not pay debt (unutilized USDS) |
| ALM Proxy | syrupUSDC balance x price | + | Mark-to-market | Prime agent revenue | Allocation tokens |
| ALM Proxy | USDC deposited into Maple | - | N/A (cost basis) | — | |
| syrupUSDC | Position value - cost basis | + | Implicit (Maple yield) | Prime agent revenue | |

## Dune queries

All queries are shared parameterized templates in [`queries/`](../../queries/) (in this repo). OBEX parameters:

| Parameter | Value |
|-----------|-------|
| `ilk_bytes32` | `0x414c4c4f4341544f522d4f4245582d4100000000000000000000000000000000` |
| `subproxy_address` | `0x8be042581f581E3620e29F213EA8b94afA1C8071` |
| `alm_proxy_address` | `0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2` |
| `venue_token_address` | `0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b` (syrupUSDC) |
| `start_date` | `2025-11-01` |
| `calendar_start_date` | `2025-11-17` |

## PnL summary (as of Apr 6 2026)

| Month | Agent Demand | Prime Agent Revenue | Agent Rate | Sky Revenue | Monthly PnL | Cumul. PnL |
|-------|-------------|--------------------:|----------:|-----------:|------------:|-----------:|
| Nov 25 (14d) | 149M | 264K | 37K | 174K | +127K | +127K |
| Dec 25 | 449M | 1,541K | 76K | 1,200K | +416K | +540K |
| Jan 26 | 579M | 2,464K | 73K | 2,030K | +507K | +1,047K |
| Feb 26 | 579M | 1,849K | 68K | 1,869K | +48K | +1,095K |
| Mar 26 | 579M | 1,971K | 72K | 1,981K | +61K | +1,157K |
| Apr 26 (5d) | 578M | 465K | 11K | 315K | +162K | +1,319K |

Monthly PnL = prime_agent_revenue + agent_rate - sky_revenue. Cumulative PnL: **~+1.32M USDS**. The strategy was most profitable during the ramp-up phase (Nov-Jan). Feb-Mar margins compressed as Maple yield converged toward the borrow rate.

## Findings

- [Nov-Dec 2025 reconciliation](findings/NOV_DEC_2025_DIFFERENCES.md)
- [Jan 2026 reconciliation](findings/JAN_2026_DIFFERENCES.md)
- [Feb 2026 reconciliation](findings/FEB_2026_DIFFERENCES.md)
