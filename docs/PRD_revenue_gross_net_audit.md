# PRD — `prime_agent_revenue` gross/net of borrowing cost (CoF) audit

**Status:** draft (2026-06-09)
**Author:** Claude Opus + lakonema2000
**Trigger:** PR #125 review of `summary.md` headline labels surfaced ambiguity about whether `prime_agent_revenue` is gross or net of Sky's BR ("cost of funds", CoF, = the interest the prime owes Sky on its ilk debt). Spark's deeply-negative net P&L (−$23M Jan–May) reinforced that the convention needs to be auditable, not just implicit.

## 1. Scope — only `prime_agent_revenue`

The other revenue quantities have a single canonical formula at a single compute site, with no hidden CoF subtraction — out of scope:

| Quantity | Definition | Ambiguity? |
|---|---|---|
| `sky_revenue (net)` | `subsidised_BR × utilized + sde_revenue − susds_spread_reimbursement − pol_agent_rate` | No — sky_revenue IS the CoF (gross of any prime credit), then minus the three credits, all at one site |
| `agent_rate` | `Σ_days subproxy_usds × ((1 + SSR + 20bps)^(1/365) − 1) + Σ_days subproxy_susds × ((1 + 20bps)^(1/365) − 1)` | No |
| `sde_revenue` | `actual_revenue × sd_share` per venue, summed | No |
| `pol_agent_rate` | SSR+20bps on sUSDS POL value (Spark S32 only) | No |
| `susds_spread_reimbursement` / `curve_susds_spread` / `psm3_susds_spread` | 30 bps × cum sUSDS value, integrated daily | No |
| `external_revenue` | per-venue Merkl / Agora claim amount (whole amount to prime) | No |
| `distribution_rewards` | Phase 3+ placeholder, currently `0` | No |

`prime_agent_revenue` is different because it's an aggregation over per-venue `actual_revenue` values, and each per-venue value comes from a different code path. The question is whether any of those code paths inadvertently subtract something CoF-equivalent before we report it.

## 2. The actual question

The convention we believe we have:

> Per-venue `actual_revenue` is **NAV growth net of new principal that arrived**, **gross of Sky's BR**. Sky's BR is captured separately via `sky_revenue` and netted at the `monthly_pnl` level. The prime's true profit = `prime_agent_total_revenue (gross)` − `sky_revenue (net)` + `sde_revenue (gross)` add-back.

Concretely, for a venue funded by USDS the prime drew from the ilk:

```
actual_revenue        = (value_eom − value_som) − period_inflow             ← gross venue yield
sky_revenue (BR leg)  = Σ_d  subsidised_BR × utilized_d                     ← CoF on the underlying ilk debt
prime_pnl_real        = (Σ actual_revenue + agent_rate) − sky_revenue + sde_revenue add-back
```

`actual_revenue` is **NAV growth — no interest deduction**. The interest the prime owes Sky lives on the OTHER side of the ledger (in `sky_revenue`) and is netted at the bottom line, not at the per-venue level. This is the principle that needs verification per venue.

## 3. Where the convention could silently break

Three concrete failure modes the audit needs to rule out:

1. **A venue source already subtracts a Sky-equivalent rate from the NAV.** Example: imagine a hypothetical sUSDS-style wrapper where the on-chain `convertToAssets` already credits SSR (Sky's savings rate) to the holder. The growth in `value_eom − value_som` would then include SSR appreciation that Sky also charges back via BR (= SSR + spread). That's a real case — we handle it via the `actual_revenue_override` mechanism for `sky_savings_token` venues, which BYPASSES the `(value_eom − value_som) − period_inflow` formula and uses the bps-spread directly. **If any new `sky_savings_token`-type venue lands without that override wiring, we'd double-count: include SSR in `actual_revenue` AND charge BR in `sky_revenue`.**

2. **A venue value reading factors in a "rebate" or "interest paid back" that's effectively CoF-equivalent.** Hypothetical example: a credit-pool token whose value drops when the pool pays out interest to senior tranches. Today no such venue exists, but the audit should confirm none of the current pricing categories has this shape.

3. **External-revenue paths could carry a CoF-net value.** Example: if an interest-sweep counterparty (Anchorage) sent us yield amounts already net of some on-chain BR-like obligation, we'd be undercounting. The audit needs to confirm each `external_revenue` path emits a gross figure.

The PRD's value is checking each per-venue path against these three failure modes and certifying "no CoF deduction here".

## 4. Per-venue audit checklist — WALKED 2026-06-09

Every current pricing category's value reading was verified to carry no CoF-equivalent deduction. The one venue type (`sky_savings_token` Cat B) where the NAV does appreciate by an SSR-equivalent rate is handled via an explicit override that bypasses the value-delta formula.

| Cat | Venue example | Value source | Citation | CoF-equivalent deduction inside the value reading? |
|-----|---------------|--------------|----------|----------------------------------------------------|
| **A** PAR_STABLE | Grove E13 RLUSD raw | `balance_of(token, ALM, block) × $1` | `prices.py:100-101` (unit price), `positions.py:216-220` (balance) | ✓ No — par-stable token, raw ERC-20 balance, no internal yield mechanism |
| **B** ERC4626_VAULT (standard) | OBEX V1 Maple syrupUSDC | `convertToAssets(shares=1, block) × par_underlying` | `prices.py:117-129` | ✓ No — Maple's `convertToAssets` reflects lender yield in the vault; Sky's BR doesn't exist in Maple's pricePerShare. The prime borrowed USDS from Sky to fund the position; Sky's BR is captured on the ilk-debt side, not the venue side. |
| **B** ERC4626_VAULT (sky_savings_token) | Spark S32 sUSDS POL | `convertToAssets(shares=1, block) × par_underlying` **+ override** | `monthly_pnl.py:2547-2563` (dispatch), `2563-2806` (sky_savings_token sub-case), `prime_agent_revenue.py:345-368` (override application) | ⚠️ YES — sUSDS appreciates by SSR. Handled correctly via `actual_revenue_override = susds_spread` (currently $0; the SSR appreciation that would otherwise land in actual_revenue is implicitly credited back to the prime via `susds_spread_reimbursement` reducing `sky_revenue (net)`). **Convention codified: any future `sky_savings_token: true` venue MUST go through this override path.** |
| **C** AAVE_ATOKEN / SPARKLEND_SPTOKEN | Grove E1 aEthRLUSD | `scaledBalanceOf × liquidityIndex × par_underlying` (via `_atoken_index_weighted_inflow`) | `monthly_pnl.py:2369-2546`, `prices.py:131-137` | ✓ No — Aave's `liquidityIndex` reflects lender yield (borrower interest, net of Aave's reserve factor). Sky's BR is not part of Aave's accounting. |
| **E** RWA_TRANCHE (Chronicle NAV) | Grove E9 JTRSY | Chronicle oracle NAV via `_resolve_rwa_nav` | `prices.py:139-144` | ✓ No — NAV is the fund's reported value (issuer-side). No Sky leg. |
| **E** RWA_TRANCHE (Centrifuge ERC-4626 fallback) | Grove E37 syrupUSDC | `convertToAssets` + exact USDC inflow from `_erc4626_event_inflow_timeseries` | `monthly_pnl.py:2945-2997` | ✓ No — same shape as Cat B Maple. |
| **F** LP_POOL (curve_stableswap) | Grove E11 Curve AUSD/USDC | `Σ reserves × per-coin price` via `_curve_lp_unit_price` (par-stable @ $1, yield-bearing recursive via `convertToAssets`) | `prices.py:146-152`, `prices.py:171-247` | ✓ No — Curve pool reserves at par. For pools with a sUSDS leg the SSR appreciation flows back to Sky via `curve_susds_spread` reducing `sky_revenue (net)` (not via the LP unit price), so the value reading itself is gross. |
| **F** LP_POOL (uniswap_v3) | Grove E12 Uniswap V3 AUSD/USDC | Position-NFT enumeration + `amount0/amount1 × $1` via `_uniswap_v3_value` | `positions.py:237-299` | ✓ No — par-stable amounts × $1. UniV3 fee accrual is to LP holders (= us) and surfaces via `external_revenue`. |
| **EOA** | Spark S23 Anchorage escrow | `balance_of(USDC, escrow, block) × $1` | `prices.py:103-115` | ✓ No — par-stable principal at par. Interest sweeps flow via `external_revenue` (Cat A `external_alm_sources` path) into S26 USDC raw. |

**Result of the walk:** every current pricing category is gross of CoF. The single exception (Cat B `sky_savings_token`) is handled by an explicit override mechanism, and the audit codifies "any future such venue MUST use the same override" as a convention.

## 5. External-revenue paths checklist

`external_revenue` feeds into `prime_agent_revenue` via the per-venue `prime_revenue` formula. Each external path needs the same CoF check:

| Path | Source | Amount semantic | CoF-net? |
|------|--------|-----------------|----------|
| **Merkl claims** (Grove E1/E3) | Dune `Claimed.amount` event | Reward token amount × NAV | No — protocol-incentive payment, no Sky leg |
| **Agora AUSD incentives** (Grove E38) | Dune transfer from configured source | USD amount | No — cash distribution |
| **Anchorage interest sweeps** (Spark S26) | Dune transfer from configured EOA | USDC amount | No — full interest payment, Anchorage is off-Sky |
| **V3 LP fees** (E11/E12) | Position-NFT `collect` events + accrual | USD-equivalent fee | No |
| **Sky governance allocations** (Spark/Grove SubProxy) | Direct USDS transfer | n/a — treated as opening balance, NOT revenue | n/a |

## 6. Other items worth confirming alongside

1. **PSM3 BR exclusion** — Sky Atlas (#e15caed7-276c-4489-95dc-9ba628566bf4) says Spark should not pay BR on USDS held in PSM3. **Confirmed implemented**: `src/settle/compute/sky_revenue.py:11` declares the deduction and `monthly_pnl.py:1977 + 3311` wires `psm_usds` through to `compute_sky_revenue_daily`. Spark's PSM3 USDS is subtracted from `utilized` — no BR charged on it.
2. **Sky-net-P&L legend in `summary.py`** — fixed in commit `a4435e8`. The legend formerly double-subtracted `pol_agent_rate` from Sky's net P&L; the correct formula is `sky_revenue (net) − agent_rate (gross) − distribution_rewards (gross)` because `pol_agent_rate` and the spread reimbursements are already inside `sky_revenue (net)`.

## 7. Deliverables

1. **Walk §4** — open each pricing-category compute site, confirm no CoF-equivalent deduction is folded into the value reading, and document the citation. One row per pricing category at minimum.
2. **Walk §5** — open each external-revenue path source, confirm the emitted amount is gross of any Sky-side deduction.
3. **Add a "Revenue conventions — gross of CoF" subsection to `docs/METHODOLOGY.md`** referencing this PRD. One paragraph: "Per-venue `actual_revenue` is NAV growth (net of new principal arriving), gross of the BR Sky charges the prime on the ilk debt that funded the venue. Sky's BR is captured separately via `sky_revenue` and netted at the `monthly_pnl` level. See [PRD link] for the per-venue audit confirming no compute path silently subtracts a CoF-equivalent amount."
4. **Invariant test** — `tests/unit/test_revenue_conventions.py` asserting:
   - `Σ venue_actual_revenue + Σ external_revenue − Σ sd_revenue == prime_agent_revenue` (aggregation invariant)
   - For each `sky_savings_token: true` venue, `actual_revenue_override` is set (catches future regressions of the only known CoF-equivalent case in the codebase)

## 8. Out of scope

- Changing the underlying compute formulas. This PRD is purely about convention, audit, and documentation — not about reshaping who gets paid what.
- Spark's deeply-negative net P&L. That's a model question (does Sky compensate Spark enough for its strategic PSM3 / POL provisioning?), not a pipeline-correctness question.
- Venue-internal fees that the NAV/index reading already nets out (Maple perf fee, Aave reserve factor, Centrifuge issuer fees, UniV3 LP fees that accrue to us). Those are venue-side costs the prime never sees and we can't ungross. They're orthogonal to the CoF question.
- Adding a new "true profit" field to provenance.json. Deferred until the audit reveals whether existing fields can be relabeled cleanly.

## 9. Next step

§4 walked 2026-06-09 — see grid above with citations. §5 is the remaining walk; the per-path expectations are pre-populated but each emit-amount should be confirmed against its Dune query / source code. Deliverables §3 (METHODOLOGY.md cross-link) and §4 (invariant test) follow.
