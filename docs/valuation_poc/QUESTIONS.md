# Valuation POC — Open questions

Follow-up items surfaced by the Dune ↔ Python POC (see [COMPARISON.md](COMPARISON.md)). Same format as [grove/QUESTIONS.md](../grove/QUESTIONS.md): one section per unresolved item, with current-state + resolution path.

---

## Q1. Uniswap V3 position pricing methodology

**Status:** Open — deferred.

No ALM holds a Uni V3 position today, so the POC could not exercise the tick-math pricer. Grove PRD §4.1 lists E12 (Uni V3 AUSD/USDC) as a planned venue.

**Interim treatment:** None. Category F is partially validated via the Curve sUSDSUSDT demo only; Uni V3 remains a methodology gap.

**To resolve:** build a Python probe against a representative Uni V3 position (even a non-ALM one, e.g. a Uniswap Foundation LP position) to validate the tick → `sqrtPriceX96` → amount0/amount1 reconstruction before E12 activates. Then add a branch to `snapshot.py` that reads `NonfungiblePositionManager.positions(tokenId)` + `pool.slot0()` and runs the Uni V3 math.

---

## Q2. Authoritative NAV for RWA tokens not on CoinGecko

**Status:** Open.

CoinGecko covers JTRSY and JAAA. It does **not** list BUIDL-I, USTB, USCC, STAC — and CoinGecko's market-implied price isn't necessarily the issuer's official NAV anyway.

| Token | Issuer | Authoritative feed | Dune-ingestable? |
|---|---|---|---|
| BUIDL-I | BlackRock | SEC filings + investor portal | No — CSV upload only |
| USTB | Superstate | `api.superstate.com` (requires API key) | CSV upload |
| USCC | Superstate | same | CSV upload |
| STAC | Securitize | Securitize Markets / PPM | Unclear what's public |
| JAAA, JTRSY | Centrifuge | `api.centrifuge.io` + on-chain pool-manager `NAVUpdated` events | Possibly on-chain |

**Interim treatment:** `$1.00` placeholder → ~10% understatement at current JTRSY price.

**To resolve:**
1. Enumerate the exact endpoint + auth method for each issuer.
2. Decide whether Python calls them directly each snapshot, or a scheduled job writes to `sky_msc.rwa_nav_daily` (see Q3).
3. Pick an authority priority — issuer API first, Centrifuge on-chain event second, CoinGecko third, $1 last.

---

## Q3. Design of `sky_msc.rwa_nav_daily` materialized table

**Status:** Open.

VALUATION_METHODOLOGY.md §5 recommends materializing RWA NAVs into a Dune table so Category E queries can join against it. Not yet built.

**Interim treatment:** RWA valuations stay Python-only.

**To resolve:** define schema — `(snapshot_date DATE, blockchain VARCHAR, token_address VARBINARY, nav_per_unit_usd DOUBLE, decimals INTEGER, source VARCHAR, updated_at TIMESTAMP)`. Decide refresh cadence (daily EOD UTC?), seeding mechanism (Dune CSV upload via API, or a community-maintained spell), and back-fill strategy (how far back do historical NAVs need to go for PnL continuity?).

---

## Q4. CoinGecko vs issuer API as authoritative price

**Status:** Open.

For JTRSY, CoinGecko returned $1.10 — a market-implied price, probably from thin secondary-market DEX trading. The issuer's official daily NAV might differ (e.g. $1.098 or $1.102). The POC used CoinGecko for convenience; it's not clear this is acceptable for settlement-grade PnL.

**Interim treatment:** CoinGecko. Documented as "market-implied, not authoritative".

**To resolve:** compare CoinGecko's JTRSY/JAAA against the Centrifuge API's published NAV for the same date, measure drift, decide tolerance. If drift > ~0.1%, escalate to issuer-API-only.

---

## Q5. Per-chain RPC coverage for non-Ethereum ALM holdings

**Status:** Open.

POC ran against Ethereum (Alchemy) + Base (`mainnet.base.org` public). Holdings also exist on:

| Chain | ALM | RPC source? |
|---|---|---|
| Arbitrum | Spark (`0x92afd6f2…`) | Alchemy key works — not yet tested |
| Optimism | Spark (`0x876664f0…`) | Alchemy key works — not yet tested |
| Unichain | Spark (`0x345e368f…`) | Alchemy supports unichain — not yet tested |
| Avalanche | Spark (`0xece6b0e8…`), Grove (`0x7107dd8f…`) | Not in current Alchemy plan |
| Plume | Grove (`0x1db91ad5…`) | Public RPC? |
| Monad | Grove (`0x94b398ac…`) | Public RPC? |

**Interim treatment:** Ethereum + Base only in the POC.

**To resolve:** probe RPC availability per chain, add per-chain endpoints to `snapshot.py`, verify each of our 8 methodologies works on the target chain (same contracts behave identically; only the chain ID and RPC URL change).

---

## Q6. ERC-4626 per-vault decoded-table coverage on Dune

**Status:** Open (formerly Q2 in VALUATION_METHODOLOGY).

The POC ran B (sUSDS) via Python only. Moving to Dune for daily timeseries requires locating per-vault `vault_evt_deposit/withdraw` tables. Coverage unknown for:
- sUSDS (Sky — custom schema?)
- syrupUSDC, syrupUSDT (Maple v2)
- fsUSDS (Spark farming)
- sparkUSDC on Base (MetaMorpho)
- grove-bbqUSDC, grove-bbqAUSD, steakUSDC (MetaMorpho)
- sparkPrimeUSDC1 (MetaMorpho variant)
- sUSDe (Ethena)

**Interim treatment:** Python `convertToAssets(balance)` per vault — one RPC call per snapshot.

**To resolve:** run `searchTablesByContractAddress` for each vault address; document the decoded-table path in ASSET_CATALOG Category B; for vaults missing decoded tables, file Dune decoding requests or fall back to `tokens.transfers` + synthetic `Deposit`/`Withdraw` reconstruction.

---

## Q7. Duplicate spDAI contracts — which is live?

**Status:** Open (carried over from VALUATION_METHODOLOGY Q5).

Two live spDAI addresses observed in `tokens.transfers`:
- `0x73e65dbd630f90604062f6e02fab9138e713edd9` — transfer activity 2025-06 → 2025-09
- `0x4dedf26112b3ec8ec46e7e31ea5e123490b05b8b` — transfer activity 2025-04 → ongoing

**Interim treatment:** POC used spUSDC to avoid the issue.

**To resolve:** read each contract's `UNDERLYING_ASSET_ADDRESS()` via RPC, confirm both point to DAI, check `totalSupply()` on each at the current block — the deprecated one should have declining supply. Exclude the deprecated address from valuation queries.

---

## Q8. Native gas coin scope — in or out of MSC PnL?

**Status:** Open (formerly Q9 in VALUATION_METHODOLOGY).

POC showed Grove ETH ALM holds 0.05 ETH = $117 in native gas. Too small to matter for PnL. But Grove Avalanche ALM received $1.57M + $713K of AVAX across 2026 (per [ALM_COUNTERPARTIES.md](../ALM_COUNTERPARTIES.md) Grove Avalanche section) — much larger.

**Interim treatment:** Python tracks native ETH via `eth_getBalance`; Dune path would require `ethereum.traces`, expensive.

**To resolve:** policy call — does native gas spend count as operational overhead (separate from prime-agent revenue), or as part of the total position-value? If separate, exclude from Category G entirely and document in a dedicated "gas costs" view. If in-scope, define per-chain oracle (Chainlink?) and ingest trace balances.

---

## Q9. Plume + Monad coverage on Dune `tokens.transfers`

**Status:** Open (formerly Q12 in VALUATION_METHODOLOGY, cross-ref [grove/QUESTIONS.md](../grove/QUESTIONS.md) Q4).

Plume ALM (`0x1db91ad5…76f812`) returns zero rows from `tokens.transfers`. Monad returns limited rows (AUSD, WMON, grove-bbqAUSD, USDC, MON only — $60M total flow tracked, likely under-counts reality).

**Interim treatment:** Ethereum-only scope for Phase 1 Dune work.

**To resolve:** wait for Dune to add Plume spell coverage; for Monad, file a decoding request for the Grove allocation tokens. Meanwhile, Python RPC against Plume/Monad public endpoints (if any) is the only path for real balances.

---

## Q10. Price-source drift between `prices.minute` and CoinGecko

**Status:** Open.

POC measured $0.01 gap on MORPHO at the same snapshot moment — 0.5% on a single token, but amplified across a large book. Different sources have different volume-weighting windows and CEX/DEX coverage.

**Interim treatment:** document which source each category uses (H uses `prices.minute` on Dune and CoinGecko on Python — they converged on the second run to $1.91).

**To resolve:** pick ONE source per token class and use it on both sides:
- Stablecoins (A): hardcoded $1.00 — no drift
- Governance tokens (H, MORPHO): `prices.minute` or CoinGecko, pick one
- Wrapped gas (G, WETH/WMON): same
- RWA NAV (E): issuer feed — authoritative by definition

---

## Q11. Python vs Dune selection rule — when to use which

**Status:** Partially resolved (POC produced §11 in VALUATION_METHODOLOGY).

The decision tree is documented but not formally adopted as house rule. In practice:
- One-off reconciliation / end-of-month settlement → Python
- Daily timeseries for dashboards → Dune

**Interim treatment:** §11 in VALUATION_METHODOLOGY is advisory.

**To resolve:** agree on the split as a standing convention; move the table into `RULES.md` so it's binding.

---

## Q12. Block-alignment convention for daily snapshots

**Status:** Partially resolved.

POC proved `block_number <=` cutoff gives exact Dune↔Python convergence. But the house rule for "which block" on a given day is still implicit.

Options:
- First block after 23:59:59 UTC of that date (calendar-aligned)
- Last block before 00:00:00 UTC of next day (same thing, different phrasing)
- Fixed block sampled at a specific wall-clock (e.g. every day at 23:50 UTC pick whatever block is latest)

**Interim treatment:** use "latest block at time of query run" — drifts by query execution time.

**To resolve:** codify in `RULES.md`. Recommend: for each `snapshot_date`, use the block whose `block_time ∈ [date + 23:59:00, date + 23:59:59]` (close enough to EOD).

---

## Q13. Reconciliation against MSC settlement forum posts

**Status:** Out of scope for POC.

[RULES.md §3](../RULES.md) notes that MSC settlement posts use APR, not APY, producing a ~1.8% overstatement. Our POC proves numerical fidelity between Dune and Python; it does not reconcile either against the forum posts.

**Interim treatment:** per-agent `findings/` folders track the discrepancies (see `agents/obex/findings/`).

**To resolve:** out of POC scope. Addressed monthly in `findings/` and [RULES.md](../RULES.md).

---

## Priority summary

High-priority (blocks production MSC from working end-to-end):
- **Q2** — BUIDL-I/USTB/USCC/STAC have no demonstrated NAV path
- **Q3** — `sky_msc.rwa_nav_daily` table design
- **Q10** — pick a canonical price source per token class

Medium-priority (reduces ambiguity, not blocking):
- **Q4** — CoinGecko vs issuer authoritativeness
- **Q5** — per-chain RPC for remaining chains
- **Q11, Q12** — codify conventions into `RULES.md`
- **Q8** — native gas scope

Low-priority (deferred, no live position or replicated methodology):
- **Q1** — Uni V3 (wait for E12)
- **Q6, Q7, Q9** — Dune-specific cleanup, tolerable while Python is viable
- **Q13** — monthly reconciliation, handled in `findings/`
