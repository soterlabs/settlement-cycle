# PRD — Grove Prime Agent PnL: Dune Queries & Dashboard

> ⚠ **HISTORICAL CONTEXT (Phase 0).** This PRD was written when Grove was scoped
> as a *pure-Dune* implementation under `msc/agents/grove/`. It is preserved
> here as background for **Phase 2** of [`settlement-cycle`](../../PRD.md), which
> re-frames Grove as a YAML-driven prime config feeding the same Extract→Normalize→Compute→Load
> pipeline used for OBEX. Use this document for **venue inventory, ilk/contract
> metadata, and pricing-strategy decisions** — ignore the file-layout and
> deliverable-location sections (they assume the old `msc/agents/grove/` layout).

**Status:** Draft (post-discovery, 2026-04-21) — superseded by settlement-cycle Phase 2 plan
**Owner:** lakonema2000
**Created:** 2026-04-21
**Original target location:** `msc/agents/grove/` (Phase 0 — Dune-only approach)

Discovery pass (2026-04-21) has resolved Q1, Q3, Q5 and constrained Q2, Q4. Remaining open items tracked in [`QUESTIONS.md`](QUESTIONS.md).

---

## 1. Background

Grove (formerly "Bloom") is a prime agent in the Sky ecosystem that mints USDS debt against ilk `ALLOCATOR-BLOOM-A` and deploys it across ~29 yield-generating venues on Ethereum, Base, Plume, Monad, and Avalanche. Unlike OBEX (single venue: Maple syrupUSDC), Grove runs a diversified RWA + on-chain credit book totalling ~$3B AUM.

The goal of this project is to replicate the monthly PnL framework already in place for OBEX (`msc/agents/obex/`), adapted for Grove's multi-venue, multi-chain, multi-asset structure.

### Reference documents
- `msc/README.md` — shared PnL framework and rate conventions
- `msc/RULES.md` — APY/SSR rules
- `msc/agents/obex/README.md` — reference implementation
- `msc/queries/shared/` — parameterized Dune queries to reuse

---

## 2. Goals

1. **Compute Grove's monthly PnL** using the same formula as OBEX: `prime_agent_revenue + agent_rate − sky_revenue`.
2. **Track every allocation** in Grove's book (all 29, currently active or not) to produce a per-venue breakdown of prime agent revenue.
3. **Publish a Dune dashboard** with: monthly PnL table, cumulative PnL line, per-venue position-value timeseries, agent demand vs. total debt, SSR history overlay, and sky/prime revenue split.
4. **Preserve reusability** — parameterize shared queries where feasible, put Grove-specific logic in `msc/agents/grove/queries/`.

### Non-goals
- No settlement-cycle math beyond monthly PnL (no weekly settlement, no late-penalty accrual).
- No cross-chain balance aggregation via manual-input tables in this phase — cross-chain gaps are flagged as known limitations (see §8).
- No reconciliation against MSC forum settlement posts (that can come later in `findings/`).

---

## 3. Contracts & addresses

Discovered via Etherscan + Alchemy RPC on 2026-04-21. All on Ethereum mainnet unless stated.

| Contract | Address | Role |
|---|---|---|
| Ilk `ALLOCATOR-BLOOM-A` | `0x414c4c4f4341544f522d424c4f4f4d2d41000000000000000000000000000000` | Grove's ilk in the Vat (32 bytes — ASCII `ALLOCATOR-BLOOM-A` + 15× `0x00`) |
| Vat | `0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B` | Source of truth for `frob`s |
| AllocatorVault | `0x26512a41c8406800f21094a7a7a0f980f6e25d43` | Calls `frob` on the Vat |
| AllocatorBuffer | `0x629ad4d779f46b8a1491d3f76f7e97cb04d8b1cd` | Intermediate USDS buffer |
| ALM Proxy | `0x491edfb0b8b608044e227225c715981a30f3a44e` | Holds all allocation positions |
| MainnetController | `0xB111E07c8B939b0Fe701710b365305F7F23B0edd` | Relayer / rate-limit policy |
| PSM | `0xf6e72db5454dd049d0788e411b06cfaf16853042` | USDS↔DAI↔USDC conversion |
| Subproxy | `0x1369f7b2b38c76B6478c0f0E66D94923421891Ba` | Holds idle USDS / sUSDS (earns agent rate) |

### Stables
| Token | Address |
|---|---|
| USDS | `0xdc035d45d973e3ec169d2276ddab16f1e407384f` |
| DAI | `0x6b175474e89094c44da98b954eedeac495271d0f` |
| USDC | `0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48` |
| sUSDS | `0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD` |
| AUSD | `0x00000000efe302beaa2b3e6e1b18d08d69a9012a` |
| RLUSD | `0x8292bb45bf1ee4d140127049757c2e0ff06317ed` |

### Start date

Confirmed by on-chain trace scan (2026-04-21):

| Date | Event | Cumul. debt |
|---|---|---|
| 2025-05-14 | First `frob` on `ALLOCATOR-BLOOM-A` — +100,000 USDS (test mint) | 100K |
| 2025-06-25 | Unwound — −100,000 USDS | 0 |
| 2025-07-24 → 2025-07-31 | Real ramp begins — +1.26B USDS over 6 frobs | ~1.26B |
| 2026-04-21 | (current) | **~$3.42B** |

Parameter values: `start_date = '2025-05-01'`, `calendar_start_date = '2025-05-14'`.

---

## 4. Allocation inventory

Source: `https://stars-api.blockanalitica.com/allocations/?star=grove`.
Interface probes and pricing notes below reflect on-chain state at 2026-04-21.

### 4.1 Ethereum (18)

E10 was dropped — the "Anemoy Tokenized Apollo Diversified Credit" token only exists on Plume per the API. See `QUESTIONS.md` Q5.
E19 was moved to Base — `0xbeef0e0834849acc03f0089f01f4f1eeb06873c9` has no code on Ethereum (`asset()` returns `0x`, `totalSupply` empty). It's the Base-only "Steakhouse Prime Instant" (= B2).

| # | Venue label | Token address | Interface | Decimals | Underlying | Pricing strategy |
|---|---|---|---|---:|---|---|
| E1 | Aave Horizon RWA RLUSD (aToken) | `0xe3190143eb552456f88464662f0c0c4ac67a77eb` | Aave aToken | 18 | RLUSD (`0x8292...17ed`) | balance × 1.0 USD |
| E2 | Aave Horizon RWA USDC (aToken) | `0x68215b6533c47ff9f7125ac95adf00fe4a62f79e` | Aave aToken | 6 | USDC | balance × 1.0 USD |
| E3 | Aave Ethereum RLUSD (aToken) | `0xfa82580c16a31d0c1bc632a36f82e83efef3eec0` | Aave aToken | 18 | RLUSD | balance × 1.0 USD |
| E4 | Grove x Steakhouse USDC (Morpho v2) | `0xbeeff08df54897e7544ab01d0e86f013da354111` | ERC-4626 ✓ | 18 (shares) | USDC (6) | `convertToAssets(balance)` |
| E5 | Grove x Steakhouse USDC High Yield (Morpho) | `0xbeef2b5fd3d94469b7782aebe6364e6e6fb1b709` | ERC-4626 ✓ | 18 | USDC | `convertToAssets(balance)` |
| E6 | Grove x Steakhouse AUSD (Morpho v2) | `0xbeeff0d672ab7f5018dfb614c93981045d4aa98a` | ERC-4626 ✓ | 18 | AUSD (6) | `convertToAssets(balance)` |
| E7 | Securitize Tokenized AAA CLO Fund (STAC) | `0x51c2d74017390cbbd30550179a16a1c28f7210fc` | Custom RWA | 6 | USDC | `$1.00` placeholder — [QUESTIONS.md](QUESTIONS.md) Q2 |
| E8 | Janus Henderson Anemoy AAA CLO (JAAA) | `0x5a0f93d040de44e78f251b03c43be9cf317dcf64` | Centrifuge tranche | 6 | USDC | `$1.00` placeholder |
| E9 | Janus Henderson Anemoy Treasury (JTRSY) | `0x8c213ee79581ff4984583c6a801e5263418c4b86` | Centrifuge tranche | 6 | USDC | `$1.00` placeholder |
| E10 | BUIDL-I (BlackRock) | `0x6a9da2d710bb9b700acde7cb81f10f1ff8c89041` | Custom RWA | 6 | USDC | `$1.00` placeholder |
| E11 | Curve AUSD/USDC LP | `0xe79c1c7e24755574438a26d5e062ad2626c04662` | Curve stableswap | 18 (LP) | AUSD+USDC | `(bal_AUSD + bal_USDC) × lp_balance / lp_totalSupply` |
| E12 | Uniswap V3 AUSD/USDC pool | `0xbafead7c60ea473758ed6c6021505e8bbd7e8e5d` | Uni V3 pool (0.01% tier) | — | token0=AUSD, token1=USDC | Positions held via NFT Position Manager — tick math (see §5.4) |
| E13 | RLUSD (raw) | `0x8292bb45bf1ee4d140127049757c2e0ff06317ed` | ERC-20 | 18 | — | balance × 1.0 |
| E14 | AUSD (raw) | `0x00000000efe302beaa2b3e6e1b18d08d69a9012a` | ERC-20 | 6 | — | balance × 1.0 |
| E15 | USDC (raw) | `0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48` | ERC-20 | 6 | — | balance × 1.0 |
| E16 | DAI (raw) | `0x6b175474e89094c44da98b954eedeac495271d0f` | ERC-20 | 18 | — | balance × 1.0 |
| E17 | USDS (raw / POL) | `0xdc035d45d973e3ec169d2276ddab16f1e407384f` | ERC-20 | 18 | — | balance × 1.0 (already subtracted from utilized) |
| E18 | sUSDS (raw / POL) | `0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD` | ERC-4626 | 18 | USDS | `convertToAssets(balance)` — only dust in ALM today |

### 4.2 Base (3)
| B1 | Grove x Steakhouse USDC High Yield (Base) | `0xbeef2d50b428675a1921bc6bbf4bfb9d8cf1461a` | Morpho 4626 | USDC |
| B2 | Steakhouse Prime Instant (Base) | `0xbeef0e0834849acc03f0089f01f4f1eeb06873c9` | Morpho 4626 | USDC |
| B3 | USDC (Base) | `0x833589fcd6edb6e08f4c7c32d4f71b54bda02913` | Circle | — |

### 4.3 Plume (3) — partially indexed in Dune
| P1 | Janus Henderson Anemoy Treasury (Plume) | `0xa5d465251fbcc907f5dd6bb2145488dfc6a2627b` | Centrifuge | USDC |
| P2 | Anemoy Tokenized Apollo Diversified Credit (Plume) | `0x9477724bb54ad5417de8baff29e59df3fb4da74f` | Centrifuge | USDC |
| P3 | USDC (Plume) | `0x222365ef19f7947e5484218551b56bb3965aa7af` | Circle | — |

### 4.4 Monad (3) — **not indexed in Dune today**
| M1 | Grove x Steakhouse High Yield AUSD (Monad) | `0x32841a8511d5c2c5b253f45668780b99139e476d` | Morpho 4626 | AUSD |
| M2 | Uniswap V3 AUSD/USDC (Monad) | `0x6b405dca74897c9442d369dcf6c0ec230f7e1c7c` | Uniswap | AUSD+USDC |
| M3 | AUSD (Monad) | `0x00000000efe302beaa2b3e6e1b18d08d69a9012a` | Agora | — |
| M4 | USDC (Monad) | `0x754704bc059f8c67012fed69bc8a327a5aafb603` | Circle | — |

### 4.5 Avalanche (2)
| A1 | Galaxy Arch CLO Token | `0x2c0adff8e114f3ca106051144353ac703d24b901` | INX RWA | USDC |
| A2 | Janus Henderson Anemoy AAA CLO (Avalanche) | `0x58f93d6b1ef2f44ec379cb975657c132cbed3b6b` | Centrifuge | USDC |

---

## 5. PnL methodology

Follows `msc/RULES.md` and OBEX's implementation.

### 5.1 Formulas

```
monthly_pnl = prime_agent_revenue + agent_rate − sky_revenue
```

**Prime agent revenue** (mark-to-market across all venues):
```
prime_agent_revenue[month] = Σ_venues (position_value_eom − position_value_som − net_inflow_during_month)
```
Equivalent to `Δ(unrealized_gain_across_venues)` when cost basis is tracked cumulatively (as in OBEX query 6954380). Cost basis per venue = cumulative USDC/AUSD/RLUSD inflow from the ALM proxy to that venue.

**Agent rate:**
```
daily_agent_rate = subproxy_usds × [(1 + SSR + 0.20%)^(1/365) − 1]
                 + subproxy_susds × [(1.002)^(1/365) − 1]
```
Grove's subproxy is `0x1369f7b2b38c76B6478c0f0E66D94923421891Ba`. On-chain snapshot at 2026-04-21: **22.68M USDS**, **0.009 sUSDS** (a single 2026-04-14 dust transfer), **753K USDC** (USDC does not earn the agent rate; noted for reconciliation). Agent rate will be non-trivial — on par with OBEX's, scaled by balance.

**Sky revenue:**
```
daily_sky_revenue = utilized_usds × [(1 + SSR + 0.30%)^(1/365) − 1]
utilized_usds = cum_frob_debt − subproxy_usds − subproxy_susds − alm_proxy_usds
```
For Grove, `alm_proxy_usds` is the USDS balance of `0x491edfb...44e`.

### 5.2 Cost basis ↔ ilk debt invariant

Per the user: cost basis in aggregate should match ilk `frob` debt. Specifically:
```
Σ_venues cum_underlying_inflow_from_alm ≈ cum_frob_debt (in USDS-equivalent units)
```
modulo: (a) PSM conversion slippage and rounding, (b) USDS held idle in the ALM proxy, (c) cross-chain transit balances (USDS bridged to other chains but not yet deployed). The daily query will expose both sides so discrepancies are visible.

### 5.3 Rate rules (unchanged from OBEX)
- APY with per-second compounding (`(1 + APY)^(d/365) − 1`).
- Borrow rate = SSR + 0.30%; agent-rate USDS = SSR + 0.20%; agent-rate sUSDS = flat 0.20%.
- SSR history per `msc/RULES.md` Rule 2. Because Grove starts 2025-05-14, the SSR `CASE` must extend back: SSR=4.50% (borrow 4.80%) through 2025-08-04, SSR=4.75% (5.05%) through 2025-10-27, SSR=4.50% (4.80%) through 2025-11-07, then continues per the OBEX boundaries.

### 5.4 Pricing strategy

| Type | Rule |
|---|---|
| USD stables (USDC, DAI, USDS, AUSD, RLUSD) | 1.0 |
| Aave aTokens | balance × 1.0 (aTokens rebase to underlying 1:1) |
| ERC-4626 vaults (E4-E6 Morpho, sUSDS) | `convertToAssets(shares)` at the daily snapshot |
| RWA tokens with off-chain NAV (E7-E10) | **`$1.00` placeholder** — tracked in [`QUESTIONS.md`](QUESTIONS.md) Q2. To be replaced once a getter or feed is identified. |
| Curve AUSD/USDC LP (E11) | `underlying_value = (bal_AUSD + bal_USDC) × lp_balance / lp_totalSupply`, where `bal_*` = `pool.balances(i)` in underlying units. Both tokens priced at 1.0. |
| Uniswap V3 position (E12) | Positions held as NFTs in the V3 Position Manager (`0xC36442b4a4522E871399CD717aBDD847Ab11FE88`). For each tokenId owned by ALM proxy: use `positions()` to get liquidity + tick range, combine with pool `slot0().sqrtPriceX96` to reconstruct `amount0` / `amount1`, price both at 1.0. |

---

## 6. Deliverables

Final artifacts (relative to the target location `msc/agents/grove/`):

### 6.1 Files
```
msc/agents/grove/
├── README.md                          # Contracts, ilk, params, PnL summary table
├── queries/
│   ├── venue_positions_ethereum.sql   # Per-venue balance timeseries on Ethereum
│   ├── venue_positions_base.sql       # Same for Base
│   ├── venue_positions_plume.sql      # Same for Plume (partial — see gaps)
│   ├── venue_positions_avalanche.sql  # Same for Avalanche
│   ├── venue_prices.sql               # Pricing: convertToAssets / NAV per venue
│   ├── grove_prime_agent_revenue.sql  # Aggregates all venues → monthly MtM delta
│   └── grove_monthly_pnl.sql          # Top-level: prime_rev + agent_rate − sky_rev
└── findings/                          # Reserved for monthly reconciliation notes
```

### 6.2 Shared-query parameters (Grove-specific values)

| Parameter | Value |
|---|---|
| `ilk_bytes32` | `0x414c4c4f4341544f522d424c4f4f4d2d41000000000000000000000000000000` |
| `subproxy_address` | `0x1369f7b2b38c76B6478c0f0E66D94923421891Ba` |
| `alm_proxy_address` | `0x491edfb0b8b608044e227225c715981a30f3a44e` |
| `venue_token_address` | **N/A** (Grove has ~28 venues across 5 chains; Phase 1 covers 18 Ethereum venues via `grove_monthly_pnl.sql`, which replaces shared query 6954380). |
| `start_date` | `2025-05-01` |
| `calendar_start_date` | `2025-05-14` |

### 6.3 Dune dashboard panels

1. **Monthly PnL table** — prime_agent_revenue, agent_rate, sky_revenue, monthly_pnl, cumulative_pnl (per month since start).
2. **Cumulative PnL line** — month-over-month cumulative.
3. **Per-venue position-value timeseries** — stacked area, top 10 venues + "other".
4. **Agent demand vs. total debt** — two lines (cum_frob_debt, utilized_usds = cum_frob_debt − ALM_proxy_USDS − subproxy balances).
5. **SSR history overlay** — step chart with SP-BEAM change boundaries.
6. **Sky revenue vs. prime agent revenue split** — stacked bar per month.

---

## 7. Implementation plan

Discovery phase is complete (2026-04-21). The plan below reflects the post-discovery, Ethereum-only Phase 1 scope.

**Phase 1 — Ethereum, stables + ERC-4626:**
1. **Build `grove_venue_positions_ethereum.sql`** — per-day `position_value` and `cost_basis` for each of the 18 Ethereum venues. Uses `ethereum.tokens.transfers` to track in/out flows to the ALM Proxy, then joins against per-venue pricing logic:
   - Stables (E13-E17) and Aave aTokens (E1-E3): `balance × 1.0`
   - Morpho 4626 vaults (E4-E6) + sUSDS (E18): `convertToAssets` via daily snapshot
   - RWA (E7-E10): `$1.00` placeholder
   - Curve LP (E11): share-of-pool math
   - Uniswap V3 (E12): NFT position reconstruction
2. **Reuse shared queries** for SSR history (6953056), agent rate (6957966), frob debt (6954382), utilized USDS (6954386). Parameterize them for Grove. The pre-Nov-2025 SSR boundaries need to be added to the `CASE`s.
3. **Write `grove_monthly_pnl.sql`** — the top-level query, parallel in shape to shared 6954380. Aggregates `prime_agent_revenue` from all venues plus `sky_revenue` from utilized USDS.
4. **README** — fill in contract table, parameter table, PnL summary.
5. **Dashboard** — 6 panels per §6.3.
6. **Validation** — check `Σ cost_basis ≈ cum_frob_debt − subproxy_usds − alm_usds − cross_chain_out`. Gaps > 1% logged in `findings/`.

**Phase 2 (deferred):** Base + Avalanche venues (add `base_venue_positions.sql`, `avalanche_venue_positions.sql`). Plume/Monad behind manual-input tables.

---

## 8. Resolved and open questions

Resolved during 2026-04-21 discovery:

| # | Topic | Resolution |
|---|---|---|
| Q1 | Subproxy | **Closed.** Grove subproxy is `0x1369f7b2b38c76B6478c0f0E66D94923421891Ba`. On-chain at 2026-04-21: 22.68M USDS, 0.009 sUSDS, 753K USDC. Agent rate query uses standard formula against this address. |
| Q3 | LP share-of-pool pricing | **Closed.** Use full math: `price_underlying × lp_balance / lp_totalSupply` with `pool.balances(i)` in underlying units. For Uni V3, reconstruct amounts from NFT position via `sqrtPriceX96` + tick range. |
| Q5 | Missing E10 Ethereum address | **Closed.** stars-api confirms Anemoy Apollo is **Plume-only**. Dropped from Ethereum inventory. Ethereum has 18 venues, not 19. |
| Q7 | Shared query compatibility | **Accepted.** Grove gets its own top-level `grove_monthly_pnl.sql`; shared SSR / frob / agent-rate queries remain parameterized and reused. |
| Q8 | Pre-Nov-2025 SSR boundaries | **Accepted.** Grove queries will extend the SSR `CASE` back to 2025-03-24 (covering Grove's 2025-05-14 start). |
| — | E19 chain | **Closed.** `0xbeef0e0834849acc03f0089f01f4f1eeb06873c9` has no code on Ethereum — Base-only. Removed from Ethereum list. |
| — | Ilk bytes32 | **Closed.** Correct value is `0x414c4c4f4341544f522d424c4f4f4d2d41000000000000000000000000000000` (32 bytes; the PRD had a typo with 33 bytes). |

Remaining open items tracked in [`QUESTIONS.md`](QUESTIONS.md):
- **Q2** — NAV/index getters for RWA tokens (E7/E8/E9/E10 + Avalanche A1/A2). Interim: `$1.00` placeholder.
- **Q4** — Plume / Monad Dune indexing gaps. Interim: Ethereum-only scope in Phase 1.
- **Q6** — cost-basis ↔ frob-debt reconciliation. Accepted as a surfaced metric, not a blocker.

---

## 9. Success criteria

- All 6 dashboard panels populate with data from Grove's start date through the current month.
- Monthly PnL table matches the OBEX table's shape (columns: Agent Demand, Prime Agent Revenue, Agent Rate, Sky Revenue, Monthly PnL, Cumulative PnL).
- `Σ cost_basis` across venues reconciles with `cum_frob_debt` to within 1% (Q6 drift budget).
- README.md populated with all contract addresses, ilk info, parameters, and PnL summary.
- All SQL files are parameterizable where it makes sense (addresses injectable for future agent re-use).
