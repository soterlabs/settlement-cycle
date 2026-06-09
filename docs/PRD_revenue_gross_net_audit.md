# PRD — Revenue gross/net audit

**Status:** draft (2026-06-09)
**Author:** Claude Opus + lakonema2000
**Trigger:** PR #125 review of `summary.md` headline labels surfaced ambiguity about whether `prime_agent_revenue` is gross or net of Sky's BR ("cost of funds", CoF). Spark's deeply-negative net P&L (−$23M Jan–May) reinforced that the convention needs to be auditable, not just implicit.

---

## 1. Problem

The settlement pipeline produces several revenue numbers that, depending on context, can be read as either gross (before some offset) or net (after it). Today the convention is implicit:

- `prime_agent_revenue` is computed per-venue as `(value_eom − value_som) − period_inflow + external_revenue − sd_revenue` and is, by design, **gross of Sky's BR** but **net of SDE redirect**.
- `sky_revenue` is the BR claim plus SDE, net of intra-Sky spread credits (sUSDS / Curve / PSM3).
- `monthly_pnl = prime_agent_total_revenue − sky_revenue` is the prime's net P&L *for non-SDE primes only*; for SDE-heavy primes the SDE redirect needs to be added back manually.

Three concrete failure modes:

1. **Per-venue NAV sources may already net something out.** If a NAV oracle or `convertToAssets` reading already factors in a vault's performance fee, an Aave reserve factor, or any other internal cost, the venue's `actual_revenue` will be silently net of that cost while we still treat it as gross. The CoF (Sky's BR) is the headline concern — but other internal deductions matter too.
2. **External revenue paths are heterogeneous.** Merkl claims (Cat C aTokens), Agora cash distributions (Grove E38), Anchorage interest sweeps (Spark S26), Curve trading fees (Cat F), V3 LP fees (E11/E12), Centrifuge NAV updates — each flows in via a different code path (`external_revenue`, `actual_revenue_override`, `inflow_by_counterparty` exception list, …). Whether each path delivers a gross or net number isn't documented anywhere.
3. **Cross-PR / cross-doc drift.** Labels in `summary.md`, names in `compute/` code, descriptions in `PRD.md`, `docs/METHODOLOGY.md`, and per-prime PRDs may diverge as new venue types land. There's no single document a reviewer can open to confirm "yes, this is gross-of-BR" with high confidence.

The cost of getting this wrong is direct dollars in the settlement output — Spark's "real profit" reading swings by tens of millions depending on whether SDE / spread credits / pol_agent_rate are added back, and we want each of those add-backs to be explicit, not folkloric.

## 2. Convention (proposed, to be confirmed)

This PRD proposes the following canonical convention. Everything else in the audit measures conformance to it.

### 2.1 `prime_agent_revenue` is gross of CoF, net of SDE redirect

| Component | Gross of | Net of |
|---|---|---|
| `prime_agent_revenue` (per prime) | Sky's BR on borrowed USDS | SDE redirect (sd_revenue) |
| `actual_revenue` (per venue) | Sky's BR | nothing on our side; **internal venue-side fees (Morpho performance fee, Aave reserve factor) are folded into the NAV/index reading and unavoidable.** |
| `sd_revenue` (per venue) | nothing | nothing (it's a pure redirect, no compute) |
| `external_revenue` (per venue) | nothing | nothing (whole amount goes to the prime) |
| `sky_revenue` (= the BR claim + SDE) | nothing additional | intra-Sky spread credits (sUSDS, Curve, PSM3) and `pol_agent_rate` |
| `agent_rate` (paid by Sky on subproxy USDS) | nothing | nothing |
| `monthly_pnl` | nothing | **only the SDE redirect — the rest is genuinely net for non-SDE primes** |

### 2.2 Sky's net P&L formula

Per `src/settle/domain/monthly_pnl.py:254`, the docstring states:

```
sky_revenue (net) = Σ_d subsidised_BR × utilized_d
                  + sde_revenue
                  − susds_spread_reimbursement
                  − pol_agent_rate
```

Therefore Sky's true net P&L is:

```
sky_pnl_net = sky_revenue (net) − agent_rate (gross) − distribution_rewards (gross)
```

Note: **`pol_agent_rate` and `susds_spread_reimbursement` are NOT subtracted again** because they're already inside `sky_revenue (net)`. The current legend in `summary.py` (introduced in PR #125) double-subtracts `pol_agent_rate` and should be corrected as part of this PRD's first deliverable.

### 2.3 Prime's true profit formula

```
prime_pnl_true = monthly_pnl + sde_revenue
              = prime_agent_revenue (gross) + agent_rate (gross) + distribution_rewards (gross)
                − sky_revenue (net) + sde_revenue
```

- For non-SDE primes (OBEX): `sde_revenue = 0` → `prime_pnl_true = monthly_pnl`
- For SDE-heavy primes (Grove): non-zero `sde_revenue` is the add-back
- For Spark: `sde_revenue` ≈ $30K–$120K/month — too small to flip the sign on its multi-million-dollar P&L

## 3. Audit checklist

This is what needs verifying before we close the gap. The PRD owner walks each item, records the actual code path, and writes "✓ gross" or "✗ unexpectedly net" plus the citation.

### 3.1 Per-venue value reads

For each pricing category, the question is: **does our SoM→EoM value comparison capture revenue gross of CoF?**

- [ ] **Cat A (par-stable)** — `balance_of(token, ALM, eom_block) − balance_of(token, ALM, som_block) − period_inflow`. Par-stable → no internal yield, no fee. Trivially gross. ✓ expected.
- [ ] **Cat B (ERC-4626 vault)** — `convertToAssets(shares, eom_block) − convertToAssets(shares, som_block) − period_inflow`. The vault's pricePerShare already nets out any performance fee inside the vault contract. Need to confirm: do we want venue.actual_revenue to be gross of vault-internal fees or accept the fee as already-deducted?
- [ ] **Cat C (aToken, scaled-balance)** — `scaledBalanceOf × liquidityIndex(eom_block) − scaledBalanceOf × liquidityIndex(som_block) − period_inflow`. The Aave `liquidityIndex` already nets out the **reserve factor** (Aave's protocol fee on borrower interest). Document this: is this acceptable as "venue-side cost we cannot avoid," or do we want to gross it back up?
- [ ] **Cat E (Centrifuge / RWA tokenized)** — NAV oracle from Chronicle or vault `convertToAssets`. Need to confirm: is the NAV gross of issuer fees or net?
- [ ] **Cat F (Curve LP)** — pool reads via `get_virtual_price` + LP balance. Fee accrual is gross (Curve fees go to LPs, who are us). ✓ expected.

### 3.2 External revenue paths

- [ ] **Merkl claims** (`_merkl_claims_revenue_usd`, Cat C aTokens) — Dune `Claimed` event amount, gross. Verify.
- [ ] **Agora AUSD incentives** (Grove E38, cash distribution path) — Verify the `cash_distribution_source` flow.
- [ ] **Anchorage interest sweeps** (Spark S23 → S26 USDC raw) — Verify the `external_alm_sources` mechanism.
- [ ] **Sky governance allocations to SubProxy** (Spark, Grove) — Routed via `get_subproxy_balance_timeseries`; treated as opening balance, no revenue attribution. ✓ correct (capital, not revenue).
- [ ] **V3 liquidity events** (E11/E12, fees + impermanent gain/loss) — Verify the `v3_liquidity_events.sql` flow.

### 3.3 Spread credits (Sky → prime, net within sky_revenue)

- [ ] **`susds_spread_reimbursement`** — Sky reimburses prime for the SSR portion of sUSDS Cat B yield, since Sky already charges BR on the underlying USDS (avoids double-charging). Routed as a `sky_revenue` reduction. Verify the magnitude matches expected (`30 bps × cum sUSDS Cat B value`).
- [ ] **`curve_susds_spread`** — Same mechanism for Curve LP with sUSDS leg.
- [ ] **`psm3_susds_spread`** — Same for PSM3 sUSDS leg.
- [ ] **`pol_agent_rate`** — Spark-specific, paid by Sky to Spark on sUSDS POL holdings; routed as a `sky_revenue` reduction. Currently labeled "(gross)" in `summary.md` because the headline value is the gross payment amount; the netting happens inside `sky_revenue (net)`.

### 3.4 Code-level conventions

- [ ] **Field names** — Adopt `_gross` / `_net` suffix on internal field names in `MonthlyPnL` and `VenueRevenue` where ambiguity exists today (low priority — `summary.md` labels are now explicit per PR #125, code-side is less reader-visible).
- [ ] **Docstrings** — Every `actual_revenue` computation site has a single-line comment "gross of Sky BR, net of {nothing | venue-internal fee X}". Currently inconsistent.

## 4. Deliverables

1. **Fix the `summary.py` legend** to remove the double-subtraction of `pol_agent_rate` from Sky's net P&L formula. Small, immediate, can ship without the rest of the audit.
2. **Run the §3 checklist** — for each box, open the relevant file, document the actual behavior in a short table in this PRD.
3. **Add a "Revenue conventions" section to `docs/METHODOLOGY.md`** referencing this PRD as the audit source of truth.
4. **Write `tests/unit/test_revenue_conventions.py`** — invariants that fail loud if a future change quietly nets BR into `actual_revenue` (e.g. assert prime_agent_revenue + sky_revenue_br_only ≈ Σ venue_gross_yield ± slip).
5. **Update `summary.md` legend** post-audit to either (a) keep the per-prime caveat or (b) drop it if the audit reveals a cleaner re-framing.

## 5. Out of scope

- Changing the underlying compute formulas. This PRD is purely about labels, conventions, and audit — not about reshaping who gets paid what.
- Spark's deeply-negative net P&L. That's a model question (does Sky compensate Spark enough for its strategic liquidity provisioning?), not a pipeline-correctness question.
- Adding a new "true profit" field to provenance.json. That's premature — let's first audit and confirm the existing fields are consistently labeled.

## 6. Next step

Walk §3.1 and §3.2 — one box per session, document the result here, ship the legend fix from §4.1 in parallel.
