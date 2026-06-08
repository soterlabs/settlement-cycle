# PRD — Spark Savings V2 Vaults (spUSDT / spPYUSD / spUSDC)

**Status:** draft, 2026-06-01. Author: settlement-cycle team.
**Scope:** compute path for Spark Savings V2 venues S56–S60 currently
skipped in `compute_monthly_pnl`. Combined TVL ≈ $1.6B (USD-stable
vaults only; spETH excluded — see §9).

---

## 1. Background

Spark runs five Savings V2 vaults — `spUSDC`, `spUSDT`, `spPYUSD`,
`spUSDC-Avalanche`, and `spETH`. The pipeline currently configures
them as venues but skips them in `compute_monthly_pnl` with a warning.
The TVL has grown to the point where omitting them materially mis-states
Spark's `prime_agent_revenue` — but the right direction of the mis-state
is *not* obvious; see §3.

### Sizing reference (verified on-chain 2026-06-01)

| Vault | Chain | Address | Underlying | totalAssets | Idle (at vault) | Deployed | Pps |
|---|---|---|---|---:|---:|---:|---:|
| spUSDC | Ethereum | `0x28b3…a43d` | USDC | $303.32M | $10.00M (USDC) | $293.32M (96.7%) | 1.0243 |
| spUSDT | Ethereum | `0xe2e7…c372` | USDT | $1270.14M | $10.00M (USDT) | $1260.14M (99.2%) | 1.0222 |
| spPYUSD | Ethereum | `0x8012…d354` | PYUSD | $0.77M | $0.77M (PYUSD) | ~$0 (0.1%) | 1.0175 |
| spUSDC-Avalanche | Avalanche-C | `0x28b3…a43d` | USDC | — | — | — | (separate) |
| spETH | Ethereum | `0xfe6e…9b8f` | WETH | — | — | — | out of scope |

**Total USD-stable TVL (Ethereum): ~$1.57B.** Pps reflects ~2 years of
VSR accrual since launch — depositors are owed `totalSupply × pps` at
any moment.

---

## 2. Verified mechanics (2026-06-01 on-chain check)

### 2.1 What the vault contract actually holds

For each of the three vaults, the only token sitting at the vault
contract address is the underlying (USDC for spUSDC, USDT for spUSDT,
PYUSD for spPYUSD). **No USDS and no sUSDS are held at the vault
address.** The "deposit to sUSDS as backing" step in the conceptual
flow happens *downstream* of the vault, at the allocator / strategy
contracts the vault delegates to.

This is consistent with an ERC-4626 vault where `totalAssets()` is a
view that *reads back* the deployed value from external contracts
rather than physically custodying it.

### 2.2 Where the deployed capital goes (7-day flow trace)

USDC outflows from spUSDC over the last 7 days (50k blocks): 257
transfers, dominated by retail withdrawal recipients (EOAs). The
**single contract destination in the top-10** is the **Spark Ethereum
ALM (`0x1601843c…347e`)** with $28.5M USDC over the window.

USDT outflows from spUSDT over the same window: 635 transfers,
dominated again by retail-withdrawal EOAs. The single contract
destination in the top-10 is again the **Spark Ethereum ALM** with
**$177.7M USDT** — the largest destination overall.

Read: the vaults' deployment route to the Spark Ethereum ALM. From the
ALM, the capital is presumably allocated to Spark's existing venue
inventory (Cat A idle, Cat C SparkLend, Cat E ERC-4626 vaults, etc.).
Strategy / allocator probing on the vault contracts returned no
`vault()`, `owner()`, `implementation()`, or MetaMorpho-style
`withdrawQueue()` — they use a custom AccessControl pattern whose
allocator addresses aren't exposed via simple selectors.

### 2.3 Consequence for the economic model

The vaults are **not** holding separate sUSDS-backed positions visible
on-chain. The deployed portion ultimately sits inside the Spark ALM's
position book, where our existing pipeline already values it (via Cat
A par-stable accounting, Cat C scaled-balance × liquidityIndex, etc.).

The original PRD draft modelled the vault as if it independently held
sUSDS and earned SSR + agent_rate + DeFi yield on the deployed
underlying — that double-counts. The deployed yield is *already* in
the existing Spark venue revenue; the savings vault only adds a
**liability** to retail depositors that our model currently ignores.

---

## 3. Economic model (revised)

### 3.1 What's already captured by the existing pipeline

For every USDS Spark mints from the Allocator Vault to back a savings
deposit:

- The USDS borrow is part of Spark's ilk debt (`cum_debt`) → Sky
  charges BR via existing `sky_revenue` machinery.
- The downstream position (USDC / USDT held at the ALM, sUSDS,
  SparkLend, Aave, …) is held *at the ALM* and is therefore tracked
  in existing Cat A / B / C / E / F venues → yield captured.
- Agent rate on subproxy sUSDS and the 30-bps spread neutralisation on
  ALM-held sUSDS are computed where applicable.

In other words: **the gross yield on the savings-vault-deployed
capital is already in Spark's `prime_agent_revenue`**.

### 3.2 What's missing

The only piece our pipeline currently ignores is the **liability to
retail depositors** that accrues at the VSR. Mechanically: the vault's
pps grows over time at the VSR, so each spUSDC / spUSDT / spPYUSD
share represents an increasing claim on the underlying. The
corresponding cost is owed by Spark.

```
vsr_liability_accrual_d = total_amount_d × vsr_d / 365
                        = total_amount_d × borrow_cost_d / 365
```

This is a daily cost on Spark's side that must be subtracted from
`prime_agent_revenue`.

### 3.3 Headline formula

```
prime_agent_revenue_savings_adjustment = − Σ_d (total_amount_d × vsr_d / 365)
                                          (summed over each vault separately)
```

No other adjustment is needed if §3.1 is correct (deployment already
tracked at the ALM). The savings vaults become a **single negative
line** in Spark's revenue breakdown, attributed per-vault.

### 3.4 Why this is cleaner than the closed-form `deployed × (apr − vsr)`

The closed-form formula proposed in `QUESTIONS.md` §S6 gives Spark's
*net spread* (the part Spark keeps). It works only if you're also
willing to *exclude* the deployment yield from the rest of the model —
which would mean removing the underlying token's contribution to the
Spark ALM's existing Cat A / C / E venues. That's harder to do
correctly and harder to reconcile.

Subtracting only the VSR-accrual liability is mathematically
equivalent (existing_revenue − vsr_liability = deployed × apr − vsr ×
principal) but doesn't require touching the existing venue
computations.

### 3.5 Sanity check: which side is bigger?

For spUSDT today (~$1.27B at VSR ~ 4.0% annual), the daily VSR accrual
is roughly $139K. Over a 30-day month, ~$4.2M.

For spUSDC (~$303M at VSR ~ 4.0%), ~$33K/day, ~$1M/month.

For spPYUSD (~$0.77M, ~$85/day) — negligible.

Combined ~$5M/month of VSR liability that should reduce Spark's
`prime_agent_revenue` once this PRD ships. The actual figure depends
on the precise VSR each day.

---

## 4. Required data sources

### 4.1 Per-vault per-day series (Dune)

`dune.sparkdotfi.result_savings_v_2_deployment_metrics`. Columns:
- `dt`, `token_symbol`
- `total_amount` — vault assets, gross (= holding + deployed)
- `holding_amount` — idle in vault contract (verified §2.1: held in
  underlying token only)
- `deployed_amount` — held downstream (verified §2.2: mostly at
  Spark ETH ALM)
- `apr` — yield rate on deployed amount (only needed for parity
  check; not consumed by the headline formula in §3.3)
- `borrow_cost` — VSR paid to depositors (the key input)

**Note to verify:** `apr` semantics — net of any vault-level
performance fee? — and whether `holding_amount` accrues VSR liability
or only `deployed_amount`. The current draft assumes VSR accrues on
`total_amount` per §3.3, but this is a deliberate choice we should
re-check against the Dune table's accrual convention.

### 4.2 No on-chain vault state needed for the headline

Given §3, we do **not** need to read `balanceOf(vault, sUSDS) ×
convertToAssets(1e18)` daily, because the vault doesn't hold sUSDS.
For a sanity check we can read `totalAssets()` and `totalSupply()` on
the vault contracts at SoM / EoM to validate the Dune `total_amount`
matches.

### 4.3 Existing streams (no changes required)

- SSR series (already in pipeline)
- BR series (already in pipeline, applied to `cum_debt`)
- Spark venue revenue (already covers the deployment yield via Cat A /
  C / E / F venues)

---

## 5. Implementation plan

### 5.1 Phase A — VSR liability subtraction (target: 1 sprint)

1. **New module:** `compute/savings_v2_liability.py` containing
   `compute_vsr_liability(prime, period, sources) → DataFrame[venue_id,
   vsr_liability_usd]`.
2. **New source interface:** `ISavingsV2Source` with method
   `daily_metrics(vault_address, chain, start, end) → DataFrame[dt,
   total_amount, holding_amount, deployed_amount, apr, borrow_cost]`.
3. **Dune implementation:** `DuneSavingsV2Source` wraps a Dune query
   reading from `result_savings_v_2_deployment_metrics`.
4. **Wire into orchestrator:** at the same point where S2 venues are
   currently skipped, instead compute the VSR liability and attribute
   it as a *negative* venue revenue to that venue (so it shows up in
   Spark's `venue_breakdown`).
5. **Validation:** assert that for each vault per day,
   `total_amount ≈ holding_amount + deployed_amount` (Dune internal
   consistency), and that `total_amount` at SoM/EoM agrees with
   on-chain `totalAssets()` within rounding.

### 5.2 Phase B — Reconciliation against on-chain implied surplus

**Implementation note (2026-06-08):** the original PRD draft proposed
reading `apr` / `borrow_cost` / `deployed_amount` from
`dune.sparkdotfi.result_savings_v_2_deployment_metrics`. That table is
now private and the existing stub
(`src/settle/normalize/sources/dune_savings_v2_deployed.py`) returns an
empty frame with a warning. Phase B therefore takes approach (a):
reconstruct the closed-form components from on-chain reads only.

For each vault per period:

```
vsr_liability_total    = Σ_d totalSupply(d-1) × Δpps(d)    ← Phase A output (provenance.json)
totalAssets_avg        = mean(totalAssets at daily EoD blocks)
holding_avg            = mean(balanceOf(vault, underlying) at daily EoD blocks)
deployed_avg           = totalAssets_avg − holding_avg
vsr_apr_eff            = (vsr_liability_total / totalAssets_avg) × (365 / n_days)
apr_eff                = (pipeline_yield_on_yield_venues / deployed_avg) × (365 / n_days)
implied_spread_bps     = apr_eff − vsr_apr_eff
net_revenue (LHS)      = pipeline_yield_on_yield_venues − vsr_liability_total
closed_form_RHS        = deployed_avg × (apr_eff − vsr_apr_eff) × (n_days / 365)
```

`pipeline_yield_on_yield_venues` is the sum of `actual_revenue` on the
hand-curated venues that hold the deployed underlying — see the
`savings_v2_routes` block in `config/spark.yaml`. The mapping is verified
via a long-window on-chain outflow trace (12-month window, 2025-05-14 →
2026-05-31) and documented in PR description text. **The Spark ALM is the
only contract destination >0.5% of vault volume for all four in-scope
vaults**, so the routing is unambiguous.

The expected per-vault identity (modulo holding-amount VSR cost):

```
LHS = RHS + holding_avg × vsr_apr_eff × n_days/365
        └──────── the VSR Spark owes on idle underlying at the vault ────┘
```

The holding-amount term is small in absolute terms (spUSDC ~ $33K/mo,
spUSDT ~ $33K/mo at current TVL), so practically `LHS ≈ RHS` is a useful
self-consistency check.

**Output:** `scripts/reconcile_savings_v2.py` reads
`settlements/{prime}/{month}/provenance.json`, performs the per-vault
math, and writes a markdown report alongside the existing artifacts.
**Display-only:** no settlement number is changed by the reconciliation;
divergences are surfaced for human review.

**Tolerance bar:** soft. A 1% LHS/RHS divergence is the canonical
"investigate" trigger, but we don't fail the pipeline — the gap is a
function of mapping completeness (which is what we're cross-checking)
and may indicate either a venue we missed in the mapping or a stable-leg
swap (USDC → USDS → sUSDS via PSM3) we haven't attributed.

### 5.3 Phase C — Avalanche path (effectively shipped via Phase A)

**Status update (2026-06-08):** Phase A's implementation took an
RPC-only path (`compute_vsr_liability_period` reads
`convertToAssets` / `totalSupply` / `totalAssets` directly) which is
chain-agnostic — the same code runs on Avalanche-C with
`venue.chain = Chain.AVALANCHE_C`. **No additional code is needed
for Phase C**; S60 has been producing real values every month since
PR #114:

| Month | S60 vsr_liability |
|---|---:|
| 2026-01 | −$651,003.18 |
| 2026-02 | −$447,213.93 |
| 2026-03 | −$362,888.86 |
| 2026-04 | −$175,138.71 |
| 2026-05 | −$94,676.37 |

The PRD's original Phase C deferral assumed a Dune-based path that
turned out to be unnecessary. What's left for Phase C is verification,
not code: spot-check that the Avalanche block resolver hits the
correct daily EoD blocks (a few sampled dates in May 2026 should
suffice) and that S60's `totalAssets()` matches BA Labs within rounding.

---

## 6. Validation

### 6.1 Per-vault internal consistency
- `total_amount ≈ holding_amount + deployed_amount` daily (Dune)
- `total_amount` at SoM/EoM matches on-chain `totalAssets()` within rounding
- `pps × totalSupply ≈ total_amount` (definitionally true; sanity check)

### 6.2 Headline parity
Run Spark Q1 2026 with the new path; expect a downward adjustment to
`prime_agent_revenue` of ~$15M for the quarter (rough magnitude:
$1.6B × 4% × 90/365 ≈ $16M). The exact figure depends on actual VSR.

### 6.3 No new double-counting
After shipping, confirm:
- The Spark Allocator-Vault USDS draws that fund the savings vaults
  are still in `cum_debt` (BR still charged).
- The yield on deployed capital is still captured in the existing
  venues (S26 / S27 / SparkLend / etc.).
- The VSR subtraction shows up *once* in `venue_breakdown`, not
  duplicated elsewhere.

### 6.4 Dune parity against Spark's own dashboard
Spark publishes deployment metrics on their own dashboards. The
monthly VSR cost per vault should agree with Spark's published
figures within rounding.

### 6.5 VSR-rate calibration (replaces former Q9 ambiguity)

Spot-check that the on-chain `pps` movement matches the published
VSR over a calibration window. Read `pps(d)` and `pps(d+30)`, then:

```
implied_vsr_per_day = (pps(d+30) / pps(d)) ^ (1/30) − 1
implied_vsr_apy     = (1 + implied_vsr_per_day) ^ 365 − 1
```

If `implied_vsr_apy` materially diverges from Spark's published VSR for
that period, the depositor accrual includes something beyond pure VSR
(performance fee, skim, separate yield path). The Phase A formula
still captures the depositor obligation exactly — but the reconciliation
in §5.2 may be harder to interpret.

---

## 7. Open questions

### 7.1 Pending — pick up next session

These four items must be resolved before merging. Each has a concrete
next action so it can be picked up cold.

| # | Question | Next action |
|---|---|---|
| **Q7** | Does the deployed savings-vault capital fully sit at the Spark Ethereum ALM, or are there off-ALM strategy contracts we're missing? | Re-run the outbound transfer trace from each vault address over a longer window (e.g. 6 months, not 7 days) and enumerate every contract destination. Cross-check against the address list Spark publishes for their stars-api / allocator registry. **Owner:** settlement-cycle team. **Blocks:** §3.1 (the whole "deployment yield is already captured" premise). |
| **Q8** | Does the Spark ALM's existing venue inventory in `config/spark.yaml` (S26 USDC raw, S27 USDT raw, SparkLend USDT/USDC, sUSDS at ALM, …) accurately price the savings-vault-deployed capital? Or is some of it held at allocator addresses not yet in the venue list? | Once Q7 enumerates allocator destinations, diff that list against current venue config. Any allocator address that *isn't* in the venue list either needs to be added or treated as an out-of-model holding. **Owner:** settlement-cycle team. **Depends on:** Q7. **Blocks:** trust in the headline VSR-only adjustment. |
| **Q3** | Is Dune `apr` net of vault performance fees? | Not blocking §3.3 (we don't consume `apr` in the headline). Becomes load-bearing in §5.2 reconciliation — if that diverges, this is the first thing to check. **Owner:** Spark, eventually; we can defer until reconciliation actually drifts. |

### 7.2 Resolved (2026-06-01)

| # | Question | Answer |
|---|---|---|
| Q1 | Agent rate applicable to vault-held sUSDS | 20 bps. Moot for the new model — vault doesn't hold sUSDS directly (§2.1). |
| Q2 | Allocator-Vault BR attribution per-vault | BR already in `cum_debt`; do not double-charge. Per-vault attribution not needed. |
| Q4 | Does `holding_amount` accrue VSR or only `deployed_amount`? | Provisionally: VSR accrues on `total_amount`. Re-verification tracked as Q9 above. |
| Q5 | spUSDC zero BR + idle composition + allocation venues | Vault holds only underlying (USDC/USDT/PYUSD). Dominant deployment destination is the Spark Ethereum ALM ($28M USDC / $178M USDT in a 7-day window). "Zero BR for spUSDC" is correct at the vault level; downstream USDS draws are correctly attributed through `cum_debt`. |
| Q6 | spETH scope | Out of scope; separate PRD if/when needed. |
| Q7 | Where does deployed savings-vault capital sit? | **Spark ALM only.** Verified 2026-06-08 via 12-month outflow trace (2025-05-14 → 2026-05-31): the Spark Eth ALM is the *only* contract destination > 0.5% of vault volume for spUSDC/spUSDT/spPYUSD; the Spark Avalanche ALM same for spUSDC-AVAX. Every other contract recipient receives ≤ $20M each (~ 0.3% of vault volume), and the EOA-inclusive trace shows those are institutional smart-wallet withdrawals (Stars-program depositors leaving), not Spark allocator addresses. |
| Q8 | Are the existing Spark venues sufficient to price the deployed capital? | **Yes, modulo cross-currency leg.** The hand-curated `savings_v2_routes` block in `config/spark.yaml` lists 6 USDC venues (S2, S8, S10, S14, S18, S26) for S56; 6 USDT venues (S3, S9, S11, S15, S24, S27) for S57; 3 PYUSD venues (S5, S25, S28) for S59; 2 USDC venues (S54, S55) for S60. Coverage is full for the same-underlying case. The remaining failure mode is stable-leg swaps via PSM3 (USDC → USDS → sUSDS / Curve) — these aren't attributed in the first cut. If Phase B reconciliation gap > 5%, extend the mapping with `S32` (sUSDS POL at ALM) weighted by the vault's share of total ALM underlying. |
| Q9 | Does VSR accrue on `total_amount` or only `deployed_amount`? | **Moot — accrual base is implicit in the chi-style ERC-4626 `pps`.** Every share grows at `(1 + VSR_per_day)` by definition of the vault's `pps()`-update mechanism; the contract has no concept of "deployed vs holding" when updating pps. Phase A's formula `supply(d-1) × Δpps(d)` is therefore exact for the depositor-side accrual, regardless of how the underlying is allocated. The original Q9 framing only mattered if we'd consumed a Dune `borrow_cost × base` product — we don't. Calibration spot-check moved to §6.5. |

---

## 8. Risks

- **Q7/Q8 unresolved → model wrong.** If a meaningful chunk of the
  deployed capital sits at allocator addresses we don't track, §3.1
  is incomplete and the VSR-only subtraction under-states Spark's
  costs. Mitigation: §5.2 reconciliation should catch this loudly.
- **Q9 accrual base.** If VSR accrues only on `deployed_amount`, our
  headline over-states the liability by `holding_amount × vsr`. Small
  effect today but worth verifying.
- **Closed-form vs decomposed drift.** If Spark changes `apr`
  semantics, §5.2 reconciliation will diverge — that's a feature, not
  a bug; loud failure prompts re-review.
- **Multi-period drift.** `pps` is the on-chain truth and grows
  monotonically; `total_amount × VSR / 365` is our reconstruction.
  Over multi-quarter windows, daily compounding vs simple-interest
  approximation will diverge by ~5–10 bps. Mitigate with daily
  granular accrual rather than monthly.

---

## 9. Out of scope

- **spETH (S58)** — backing mechanism is different (not sUSDS-based);
  separate PRD.
- **Per-allocation breakdown** — interesting reporting but not needed
  for the headline number; deferred.
- ~~**Avalanche spUSDC (S60)**~~ — shipped via Phase A's chain-agnostic
  RPC path (§5.3). Verification spot-check still recommended (sample
  daily EoD blocks on Avalanche against on-chain `totalAssets()`).
- **Performance attribution between Spark and depositors at the per-day
  granularity** — the headline number captures the netted P&L; we
  don't try to reconstruct *which* deployment leg outperformed.

---

## 10. Success criteria

1. ✓ **Done (PR #114):** S56, S57, S59, S60 venues produce non-zero
   (negative) `vsr_liability` contributions in Spark's monthly settlement
   breakdown (Jan–May 2026: total ~$28M Spark CoF across the four vaults).
2. Per-vault internal consistency checks (§6.1) pass.
3. **Ongoing — per-period monitoring via `scripts/reconcile_savings_v2.py`.**
   Closed-form reconciliation lands within 1% — or the gap is
   characterised and the model is corrected accordingly. Per §5.2 the
   tolerance is soft (an "investigate" trigger, not a pipeline gate);
   per-vault gaps are surfaced in `savings_v2_reconciliation.md` and
   reviewed each month.
4. ✓ **Done (PR #114):** Headline `prime_agent_revenue` for Spark
   decreased by the expected VSR liability magnitude (verified against
   BA Labs Jan–May 2026 to within $5K/month per vault).
5. ✓ **Done (2026-06-08):** Q7, Q8, Q9 resolved before merging to main.

---

## 11. Verification log

**2026-06-01 — initial on-chain check (Ethereum mainnet, latest block):**
- spUSDC vault holds 10,002,994.27 USDC, 0 USDS, 0 sUSDS at its contract address.
- spUSDT vault holds 10,000,186.48 USDT, 0 USDS, 0 sUSDS at its contract address.
- spPYUSD vault holds 773,066.16 PYUSD, 0 USDS, 0 sUSDS at its contract address.
- ERC-4626 `totalAssets()` returned: spUSDC $303.32M, spUSDT $1270.14M, spPYUSD $0.77M.
- Pps returned: spUSDC 1.024309, spUSDT 1.022201, spPYUSD 1.017519.
- 7-day USDC outflow trace from spUSDC: 257 transfers, only contract destination in top-10 is the Spark Eth ALM ($28.5M); all others are EOAs (retail withdrawals).
- 7-day USDT outflow trace from spUSDT: 635 transfers, only contract destination in top-10 is the Spark Eth ALM ($177.7M); all others are EOAs.

**2026-06-08 — 12-month outflow trace (Q7 resolution):**
- Window: 2025-05-14 → 2026-05-31 (the prime-active range).
- Method: `tokens.transfers` for each vault's underlying, group by `to`,
  filter on join against `{chain}.creation_traces` for contract recipients only.
- **S56 spUSDC ETH:** $2.64B → Spark Eth ALM (2,170 tx); next contract
  $20M (0.7%). Conclusion: ALM is the sole allocation destination.
- **S57 spUSDT ETH:** $7.23B → Spark Eth ALM (2,377 tx); next contract
  $11M (0.15%). Conclusion: same.
- **S59 spPYUSD ETH:** $5.68M → Spark Eth ALM (26 tx); no other contract
  recipients in top-25. Conclusion: same.
- **S60 spUSDC AVAX:** $700.95M → Spark Avalanche ALM (1,769 tx); no other
  contract recipients in top-25 from the contract-only filter. Conclusion: same.
- Effect: PRD Q7/Q8 resolved. The `savings_v2_routes` mapping in
  `config/spark.yaml` is grounded in this trace.
