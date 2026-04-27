# Grove — Open Questions

Running list of unresolved items. Each question names the blocker and the fallback in the meantime.

---

## Q2. NAV / index getters for RWA tokens

**Status:** Open. Using `1.0 USD` placeholder.

On-chain probes (2026-04-21) show none of these tokens expose a standard `asset()` / `convertToAssets()` / NAV getter that we can read from Dune:

| Venue | Address | decimals | `asset()` | Notes |
|---|---|---:|---|---|
| E7 Securitize CLO (STAC) | `0x51c2d74017390cbbd30550179a16a1c28f7210fc` | 6 | revert | Symbol `STAC`. No 4626 interface. Needs Securitize index feed. |
| E8 Janus Anemoy AAA CLO (JAAA) | `0x5a0f93d040de44e78f251b03c43be9cf317dcf64` | 6 | revert | Centrifuge tranche token. NAV published via Centrifuge pool manager / off-chain. |
| E9 Janus Anemoy Treasury (JTRSY) | `0x8c213ee79581ff4984583c6a801e5263418c4b86` | 6 | revert | Same as E8. |
| E11 BUIDL-I | `0x6a9da2d710bb9b700acde7cb81f10f1ff8c89041` | 6 | revert | BlackRock. NAV set by issuer; may rebase balances or need external feed. |
| A1 Galaxy Arch CLO Token (Avax) | `0x2c0adff8e114f3ca106051144353ac703d24b901` | ? | not probed | INX RWA. |
| A2 Janus Anemoy AAA CLO (Avax) | `0x58f93d6b1ef2f44ec379cb975657c132cbed3b6b` | ? | not probed | Centrifuge. |

**Interim treatment:** price each at **$1.00 per smallest-unit-adjusted token** (i.e. `balance / 10^decimals`). This understates MtM by the accrued coupon. All five are roughly-par RWA tokens so the drift per month is small (coupon ≈ 4–6% annualised, so ~0.5%/month max vs. cost basis if we were tracking yield correctly).

**To resolve:** per-token investigation — check if there's a Chainlink/on-chain NAV oracle, a Centrifuge price feed, or a Securitize proxy with a price getter. Once found, replace the `1.0` placeholder with the read.

---

## Q4. Cross-chain indexing (Plume, Monad)

**Status:** Open. Ethereum-only scope in Phase 1.

- **Plume** (3 venues: P1–P3) — partially indexed in Dune (`plume.*` tables exist for some primitives).
- **Monad** (4 venues: M1–M4) — **not indexed in Dune at all** today.
- **Base** (3 venues: B1–B3) and **Avalanche** (2 venues: A1–A2) — fully indexed.

**Interim treatment:** queries cover Ethereum only. Cost basis reconciliation against `cum_frob_debt` will show the bridged-out USDS as a persistent gap (see §5.2 / Q6 of PRD).

**To resolve:** either (a) add `base.*`, `avalanche_c.*` in Phase 2 once Ethereum is validated, and (b) build a manual-input table for Plume/Monad balances populated by an off-chain script (per user direction — deferred).

---

## Q5. E10 "Anemoy Tokenized Apollo Diversified Credit" on Ethereum

**Status:** Closed — does not exist on Ethereum.

stars-api (`https://stars-api.blockanalitica.com/allocations/?star=grove`) only returns the Apollo token on **Plume** at `0x9477724bb54ad5417de8baff29e59df3fb4da74f`. No Ethereum counterpart is listed.

**Resolution:** drop E10 from the Ethereum allocation inventory. Grove Ethereum has **18 venues**, not 19. If an Ethereum version is later deployed, re-add it.

---

## Q9. Aave aToken rebase undercount (E1–E3)

**Status:** Open. Interim treatment accepted for Phase 1.

Aave v3 aTokens (E1 aRLUSD-Horizon, E2 aUSDC-Horizon, E3 aRLUSD) scale balances silently through the reserve `liquidityIndex`. `tokens.transfers` emits the **nominal** amount at the time of each deposit/withdraw — interest accruing between events is invisible in the transfer log.

Current treatment in `grove_venue_positions_ethereum.sql`:
- `cum_shares` = Σ of transfer deltas (ignores silent rebase).
- `price_per_share_usd` = 1.0, `net_cost_basis_usd` = `cum_shares × 1.0`.
- ⇒ `unrealized_gain_usd` ≡ 0 for aave venues. Yield is invisible in `position_value` and only crystallizes as a realized positive `daily_net_shares` delta when Grove withdraws more than it deposited.

**Impact:** In-flight Aave interest (a few bps per month at current supply APY) is under-counted in monthly PnL until the next withdrawal event. Realized PnL over the full holding period is still correct; intra-month mark-to-market is not.

**To resolve:** scale the aToken balance by the reserve `liquidityIndex` at EOD. Options:
- (a) Read Aave v3 `ReserveDataUpdated` events from `aave_v3_ethereum.Pool_evt_ReserveDataUpdated` and index-multiply daily.
- (b) Use `aave_v3_ethereum.interest_rate` if Dune exposes a pre-computed index/rate table.
- (c) Keep interim; accept the monthly cadence lag and document in the dashboard footnote.

---

## Q10. E11 Curve LP and E12 Uniswap V3 deferred

**Status:** Open. Excluded from `grove_venue_positions_ethereum.sql` in Phase 1.

- **E11** — Curve stableswap AUSD/USDC LP. Correct valuation requires daily `pool.balances(0)` + `pool.balances(1)` + `pool.totalSupply()` to compute share-of-pool × underlying reserves. Dune has `curvefi_ethereum.vyper_contract_*` event tables but no pre-joined EOD snapshot.
- **E12** — Uniswap V3 AUSD/USDC concentrated-liquidity NFT. Requires reading position params from the NFT Position Manager (`tickLower`, `tickUpper`, `liquidity`) and computing token amounts at the current `sqrtPriceX96`; Dune's `uniswap_v3_ethereum.positions` + `factory_pool_created` gives the hooks but the math must be reconstructed.

**Interim treatment:** exclude both from position_value totals. Cost basis for both is the USDC/AUSD that flowed out of ALM to the pool/NFT contract — this *will* show as a persistent gap against `cum_frob_debt` in the reconciliation (PRD §5.2 / Q6).

**To resolve:** build a follow-up query `grove_lp_positions_ethereum.sql` with:
- Curve: daily snapshot via `contract_read` / eth_call on `get_virtual_price()` × LP balance, OR reconstruct from `TokenExchange` + `AddLiquidity` + `RemoveLiquidity` events.
- Uni V3: read position once per deposit event and re-price daily at current tick (fee accrual via `Collect` events).
