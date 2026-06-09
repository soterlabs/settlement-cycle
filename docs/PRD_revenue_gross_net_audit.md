# PRD — `prime_agent_revenue` gross/net audit

**Status:** draft (2026-06-09)
**Author:** Claude Opus + lakonema2000
**Trigger:** PR #125 review of `summary.md` headline labels surfaced ambiguity about whether `prime_agent_revenue` is gross or net of Sky's BR ("cost of funds", CoF). Spark's deeply-negative net P&L (−$23M Jan–May) reinforced that the convention needs to be auditable, not just implicit.

## 1. Scope — why only `prime_agent_revenue`

The other revenue quantities in the pipeline each have a single, unambiguous definition in code and only one place where they're computed:

| Quantity | Definition | Ambiguity? |
|---|---|---|
| `sky_revenue (net)` | `subsidised_BR × utilized + sde_revenue − susds_spread_reimbursement − pol_agent_rate` (one formula, one site) | No |
| `agent_rate` | `Σ_days subproxy_usds × ((1 + SSR + 20bps)^(1/365) − 1) + Σ_days subproxy_susds × ((1 + 20bps)^(1/365) − 1)` | No |
| `sde_revenue` | `actual_revenue × sd_share` per venue, then summed | No |
| `pol_agent_rate` | SSR+20bps on sUSDS POL value (Spark S32 only) | No |
| `susds_spread_reimbursement` / `curve_susds_spread` / `psm3_susds_spread` | 30 bps × cum sUSDS value, integrated daily | No |
| `external_revenue` | per-venue Merkl / Agora claim amount (whole amount goes to prime) | No |
| `distribution_rewards` | Phase 3+ placeholder, currently `0` | No |

These are out of scope — they're one-way ledger entries computed at a single site from explicit inputs.

`prime_agent_revenue` is different: it's an **aggregation** over per-venue `actual_revenue` values, and each per-venue value comes from a different code path with different upstream guarantees. That's where hidden netting can creep in unnoticed.

## 2. The actual question

Per `src/settle/compute/prime_agent_revenue.py:322`:

```
prime_revenue (per venue) = actual_revenue − sd_revenue + external_revenue
prime_agent_revenue       = Σ prime_revenue (over venues)
```

The convention we WANT to hold:

> `actual_revenue` is **gross of every external cost the prime pays**, including Sky's BR.
> Sky's BR is captured separately via `sky_revenue` and netted at the `monthly_pnl` level.

This convention IS correct for Cat A par-stables (zero internal yield, zero internal fee — `value_eom − value_som ≡ 0` trivially gross). But for yield-bearing venue types it depends on what the **underlying value reading** (NAV oracle / index / `convertToAssets`) already nets out internally.

If a venue's value reading already nets out some internal cost (e.g. Aave's reserve factor, Morpho's vault performance fee, Centrifuge's issuer fee), then `actual_revenue` is **silently net of that cost** while we treat it as gross. The CoF (Sky's BR) isn't the issue — it's never inside a venue NAV. But internal venue-side fees ARE inside the NAV, and the question is: do we want them treated as "unavoidable venue-side cost" (current behavior, accepted implicitly) or grossed back up (would require fee-rate inputs per venue, not viable for most third-party vaults)?

The honest answer is **the current "accept the venue-internal netting" behavior is probably the right call** — we can't ungross what we can't observe. But the PRD's value is **making this explicit per venue**, so a reviewer auditing a single number can immediately see which costs are inside and which aren't.

## 3. Per-venue audit checklist

For each pricing category, fill in the table:

| Cat | Venue example | Value source | Internal nettings (folded into our `actual_revenue`) | Gross of CoF? |
|-----|---------------|--------------|------------------------------------------------------|---------------|
| **A** | Grove E13 RLUSD raw | `balance_of(token, ALM, block)` | None — par-stable, no internal yield | ✓ (trivially) |
| **B** | OBEX V1 Maple syrupUSDC | `convertToAssets(shares, block)` | Maple **performance fee** (taken from vault yield before mint of new shares) | ✓ for our purposes |
| **B** | Spark S32 sUSDS POL | `convertToAssets(shares, block)` | sUSDS SSR accrual (Sky-internal) | ✓ — `actual_revenue_override` injects the 30bps spread, not the raw NAV |
| **C** | Grove E1 aEthRLUSD | `scaled_balance × liquidityIndex` | Aave **reserve factor** (skimmed from borrower interest before being credited to lenders' index) | ✓ for our purposes |
| **E** | Grove E9 JTRSY | Chronicle NAV oracle | Anemoy / Centrifuge **issuer fees** (folded into NAV) | ✓ for our purposes |
| **E** | Grove E37 syrupUSDC | `convertToAssets` (ERC-4626 fallback) | Maple performance fee | ✓ for our purposes |
| **F** | Grove E11 Curve AUSD/USDC | `get_virtual_price` + LP balance | None — Curve fees accrue to LP holders (= us) | ✓ |
| **F** | E12 Uniswap V3 LP | Position-NFT events + pool reads | UniV3 **swap fee** (already accrues to position) | ✓ |

"✓ for our purposes" = the underlying source already nets the venue-internal fee out of the NAV before we read it. We accept this as unavoidable (we don't have the inputs to gross it back up, and the fee is genuinely a venue-side cost the prime never sees). Gross-of-CoF (Sky's BR) is preserved.

**The grid is empty until walked.** Filling it is the deliverable.

## 4. External-revenue paths

`external_revenue` feeds into `prime_agent_revenue` (via the per-venue `prime_revenue` formula in §2). Each external path needs the same gross/net check:

| Path | Source | Value emitted | Gross of CoF? |
|------|--------|----------------|---------------|
| **Merkl claims** (Grove E1/E3) | Dune `Claimed` event amount | aToken amount × NAV | ✓ — `Claimed.amount` is the full reward, no fee netted |
| **Agora AUSD incentives** (Grove E38) | Dune transfer from configured `cash_distribution_source` | USD amount | ✓ — direct cash distribution |
| **Anchorage interest sweeps** (Spark S26) | Dune transfer from configured `external_alm_sources` EOA | USDC amount | ✓ — full interest payment |
| **V3 LP fees** (E11/E12) | Position-NFT `collect` events + accrual | USD-equivalent fee amount | ✓ — full LP fee |
| **Sky governance allocations** (Spark/Grove SubProxy) | Direct USDS transfer | n/a — treated as opening balance, NOT revenue | ✓ (correctly classified as capital, not yield) |

Same caveat: the grid is the deliverable, the writeup is the spec.

## 5. Other items worth confirming alongside

These aren't gross/net concerns per se but live in the same accounting neighborhood and are worth checking while the file is open:

1. **PSM3 BR exclusion** — Sky Atlas (#e15caed7-276c-4489-95dc-9ba628566bf4) says Spark should not pay BR on USDS held in PSM3. **Confirmed implemented**: `src/settle/compute/sky_revenue.py:11` declares the deduction and `monthly_pnl.py:1977 + 3311` wires `psm_usds` through to `compute_sky_revenue_daily`. Spark's PSM3 USDS is subtracted from `utilized` — no BR charged on it.
2. **Sky-net-P&L legend in `summary.py`** — fixed in commit `a4435e8`. The legend formerly double-subtracted `pol_agent_rate` from Sky's net P&L; the correct formula is `sky_revenue (net) − agent_rate (gross) − distribution_rewards (gross)` because `pol_agent_rate` and the spread reimbursements are already inside `sky_revenue (net)`.

## 6. Deliverables

1. **Walk §3** — open each pricing-category source file, document the actual venue-internal nettings, mark ✓ or note exceptions. One row per pricing category at minimum.
2. **Walk §4** — open each external-revenue Dune query / source path, document what the emitted value contains.
3. **Add a "Revenue conventions" subsection to `docs/METHODOLOGY.md` §2.5** linking to this PRD as the audit source of truth. One paragraph: "`prime_agent_revenue` is gross of Sky's BR but already net of unavoidable venue-internal fees that live inside the NAV reading — see [PRD link] for the per-venue breakdown of which fees are folded in where."
4. **(Optional) Invariant test** — a `tests/unit/test_revenue_conventions.py` that asserts `Σ venue_actual_revenue + Σ external_revenue − Σ sd_revenue == prime_agent_revenue` (the aggregation invariant) for the OBEX/Grove/Spark fixtures. This doesn't catch hidden venue-internal netting but does catch future regressions in the per-venue aggregation rule.

## 7. Out of scope

- Changing the underlying compute formulas. This PRD is purely about labels, conventions, and audit — not about reshaping who gets paid what.
- Spark's deeply-negative net P&L. That's a model question (does Sky compensate Spark enough for its strategic liquidity provisioning?), not a pipeline-correctness question.
- Adding a new "true profit" field to provenance.json. The `prime_agent_revenue (net)` row in `summary.md` (from PR #125) plus the per-prime caveat in the legend is enough; deferring a dedicated field until after the audit reveals whether the existing fields can be relabeled cleanly.

## 8. Next step

Walk §3 first (one row per pricing category, single sitting). The grid moves from "empty" to "filled" with citations; the spec moves from "assumed" to "documented". §4 follows in a second sitting.
