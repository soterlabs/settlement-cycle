# PRD — sUSDS attribution Case 3 (PSM3 + Curve sUSDS legs)

**Status:** scoping (2026-06-10)
**Branch:** `fix/susds-curve-psm3-attribution`
**Predecessor:** PR #130 (Case 1 — clean POL: S37/S43/S47/S51)
**Reference:** `docs/PRD_revenue_gross_net_audit.md` §10 (the colleague's 3-case writeup)

## Scope

Case 3 is the sUSDS attribution gap for sUSDS that lives **inside other positions** rather than as a standalone venue:

1. **PSM3 sUSDS leg** — the sUSDS portion of PSM3 reserves on L2s (Base / Arb / Op / Uni). PSM3 isn't a venue; it's a deduction from `utilized`. Today, `_psm3_susds_spread` credits 30bps × `cum_susds` per day to the prime (via a `sky_revenue` reduction), but the prime's full SSR × V appreciation inside PSM3 is never booked in `prime_agent_revenue`. Same shape as Case 1 — asymmetric accounting: prime physically holds the appreciation but the books don't reflect it.

2. **Curve sUSDS leg (S24)** — the sUSDS coin slice of Spark's Curve sUSDS/USDT pool. The colleague flagged this as Case 3, but **the bug shape may differ from PSM3**: S24 is a Cat F venue and its `actual_revenue` IS computed via `_curve_lp_unit_price`, which DOES recursively call `convertToAssets` on the sUSDS leg and embeds the SSR appreciation into `value_eom − value_som`. The question is then how the SDE redirect on the USDT leg interacts with this — see §4 below.

## Estimated impact

Computed from settlements' `provenance.json` Jan–May 2026 (Spark only — Grove + OBEX have no PSM3 sUSDS or sUSDS-leg Curve pools):

| Sub-case | Σ existing spread credit | Implied SSR × V (rough × SSR/30bps ≈ 17) |
|---|---:|---:|
| PSM3 sUSDS leg | $423,254 | ~$7,054,000 |
| Curve sUSDS leg (S24) | $21,728 | ~$362,000 |
| **Total Case 3** | $444,982 | **~$7.4M** |

Combined with Case 1's $4.77M closure: cumulative ~$12.2M of Spark's apparent $23M loss is recoverable. The remaining ~$11M is Case 2 (S32, ~$10M, blocked on `savings_v2_deployed`) + Anchorage timing + minor noise.

## Implementation plan (sub-case 3a: PSM3 sUSDS)

**Today**'s accounting for PSM3 sUSDS:
- Sky's BR machinery charges `BR × utilized` where `utilized` does NOT deduct the PSM3 sUSDS leg (only the USDS leg is deducted). So BR effectively applies to the sUSDS slice.
- `_psm3_susds_spread` computes `Σ_d cum_susds_d × spread_factor` (30 bps daily) and reduces `sky_revenue (net)` by that amount.
- The prime physically holds the SSR × V appreciation inside PSM3 (visible via `convertToAssetValue` growth on the prime's PSM3 shares), but no field on the prime side accounts for it.
- Net per leg: Sky gets `BR × V − 30bps × V = SSR × V`. Prime physically receives `SSR × V` but the books say it received `0`. → `monthly_pnl` understates by `SSR × V`.

**Proposed fix:**

Add a new helper `_psm3_susds_appreciation(psm_usds, period, ssr_df)` that returns the full SSR × V per period (using the per-day SSR rate, not the 30 bps spread):

```python
def _psm3_susds_appreciation(psm_usds, period, ssr_df) -> Decimal:
    """Daily-integrated SSR appreciation on the prime's PSM3 sUSDS leg.
    
    Returns Σ_d  cum_susds_d × ((1 + SSR_d)^(1/365) − 1)
    
    Booked as a Prime Revenue addition. Pairs against the SSR × V that
    Sky already charges via the BR machinery (full BR on cum_debt, no
    utilized reduction for the sUSDS leg, minus the existing 30bps
    `psm3_susds_spread` reimbursement). The two cancel in monthly_pnl.
    """
```

Add it to `prime_agent_revenue` via a new field on `MonthlyPnL` (e.g. `psm3_susds_appreciation`) or by folding it directly into the prime_revenue sum. Either way:
- `prime_agent_revenue (gross)` rises by `+SSR × V`
- `sky_revenue (net)` unchanged
- `monthly_pnl` rises by `+SSR × V`

`_psm3_susds_spread` stays as-is — it's the legitimate 30 bps reimbursement, separate from the SSR appreciation.

**Care to take with the daily SSR rate.** The existing `_psm3_susds_spread` uses a constant `daily_compounding_factor(BASE_RATE_OVER_SSR)` (30 bps daily, hardcoded). For the new appreciation function we need the **per-day SSR rate** from `ssr_df`, mirroring how `compute_sky_revenue_daily` reads SSR per day. Reuse that machinery.

## Implementation plan (sub-case 3b: S24 Curve)

**Open question — is the S24 bug actually the same shape as PSM3?**

For S24 the LP unit price embeds the sUSDS leg's SSR appreciation (`prices.py:_curve_lp_unit_price` recursively calls `convertToAssets`). So `value_eom − value_som` already captures the SSR component as part of LP appreciation. But:

1. S24 has `sd_share = 100%` (USDT leg is SDE, capped). So `sd_revenue = actual_revenue` and `prime_revenue (per venue) = 0`.
2. The 100% SDE redirect captures the entire LP appreciation as `sde_revenue`, including the SSR portion.
3. Sky ALSO charges BR on the underlying USDS, less the 30 bps `curve_susds_spread` reimbursement.

So Sky **might be double-counting** the SSR on S24: once via SDE (`actual_revenue × 1.0`) and once via the BR machinery (`BR × utilized − 30 bps reimb`). Need to verify by computing what the sd_share cap should be — if it's `min(cap, value)` and `cap` is the USDT-leg value (par), then `sd_revenue` is capped at the USDT portion, not the full LP. In which case the SSR portion stays in `prime_revenue` and there's no double-count.

**Action item before implementing:** look at the SDE-share computation for S24 (`_capped_sd_revenue_daily_resolved` in `prime_agent_revenue.py`) and walk a concrete period to check whether SSR appreciation ends up in `sd_revenue` (= Sky takes it) or `prime_revenue` (= prime takes it). Only after that can we say what S24's bug actually is.

## Sequencing

1. **PSM3 sub-case (3a) first** — bigger impact ($7M), cleaner mechanism (PSM3 isn't a venue, just add a Prime Revenue line). Lay down the per-day SSR helper, the new field, the wiring into prime_agent_revenue, the per-venue display (if any), and the unit test.
2. **Verify S24 (3b)** via the §3b investigation. If the bug shape is confirmed (SSR double-count via SDE + BR), the fix is reshaping the SDE cap or excluding SSR from the SDE-eligible portion. If the SDE cap is already on USDT-only value, S24 may not need a code change.
3. **Update PRD §10** with confirmed impact, re-run pipelines, open PR.

## Out of scope

- Case 2 (S32 mixed-source) — still blocked on `savings_v2_deployed` data source.
- Changing the existing 30 bps reimbursement mechanism (`_psm3_susds_spread`, `_curve_susds_spread`, `susds_spread_reimbursement`). Those remain Sky-side credits. The fix is additive: add the SSR × V appreciation to the prime side.

## Notes for whoever picks this up

- The daily SSR rate is in `ssr_df` (already passed to `compute_sky_revenue_daily`). Don't introduce a constant SSR or read it from elsewhere.
- The `daily_compounding_factor` helper in `_helpers.py` takes an APY and returns the daily factor — use it for consistency with the existing BR / spread integration.
- The `cum_susds` series in `psm_usds` is in sUSDS shares per the docstring; verify the unit before multiplying (USDS-equivalent vs sUSDS-share matters for the SSR × V product).
- For the per-venue display in `summary.md`, the PSM3 appreciation isn't tied to any single venue — it's a prime-level credit. Decide whether to surface it as a new headline row (e.g. `psm3_susds_appreciation (gross)`) or fold it into `prime_agent_revenue (gross)` silently. The former is more transparent.
