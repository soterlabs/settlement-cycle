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

## 4. Per-venue audit checklist

For each pricing category, fill in:

| Cat | Venue example | Value source | CoF-equivalent deduction inside the value reading? |
|-----|---------------|--------------|----------------------------------------------------|
| **A** | Grove E13 RLUSD raw | `balance_of(token, ALM, block)` | No — par-stable, raw balance |
| **B** | OBEX V1 Maple syrupUSDC | `convertToAssets(shares, block)` | No — the prime borrowed USDS from Sky to deploy here; Sky's BR doesn't appear in Maple's NAV |
| **B** | Spark S32 sUSDS POL | `convertToAssets(shares, block)` + **override** | YES — sUSDS appreciates by SSR. Handled correctly via `actual_revenue_override` (bypasses the value-delta formula). **Convention: any future `sky_savings_token: true` venue MUST set the override.** |
| **C** | Grove E1 aEthRLUSD | `scaled_balance × liquidityIndex` | No — Aave's `liquidityIndex` reflects lender yield, doesn't include Sky's BR |
| **E** | Grove E9 JTRSY | Chronicle NAV oracle | No — NAV is the fund's reported value, no Sky-side deduction |
| **E** | Grove E37 syrupUSDC | `convertToAssets` fallback | No (same as Cat B Maple) |
| **F** | Grove E11 Curve AUSD/USDC | `get_virtual_price` + LP balance | No — pool yield only, no Sky-side deduction |
| **F** | E12 Uniswap V3 LP | Position-NFT events + pool reads | No |

"No" rows are the expected state. Any "Yes" must be paired with an explicit code-side mechanism (override, exception, or netting reversal) that prevents the double-count. The grid is empty until walked.

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

Walk §4 first (one row per pricing category, single sitting). The grid moves from "expected" to "confirmed" with citations. §5 follows in a second sitting.
