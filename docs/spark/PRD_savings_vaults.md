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

### 5.2 Phase B — Reconciliation against Dune's closed-form surplus

Compute the closed-form `deployed_d × (apr_d − vsr_d) / 365` in
parallel for each vault. This should reconcile to:

```
existing_prime_revenue_contribution_from_deployed_capital − vsr_liability ≈ closed_form_surplus
```

Where the LHS is the existing-pipeline yield attributable to the
deployed capital (the part that flows through the Spark ALM venues).
Quantifying that yield exactly requires identifying which ALM positions
correspond to which vault — see §7 Q1 below. For a coarse check we can
sum yield across the most likely venues (S26 USDC raw, S27 USDT raw,
SparkLend USDC / USDT, sUSDS at ALM) and compare.

A 1% reconciliation tolerance is the success bar.

### 5.3 Phase C — Avalanche path (deferred)

`spUSDC-Avalanche` (S60) is small. Defer until Ethereum lands and
reconciles. Same model; needs chain-aware Dune source.

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

---

## 7. Open questions

### 7.1 Pending — pick up next session

These four items must be resolved before merging. Each has a concrete
next action so it can be picked up cold.

| # | Question | Next action |
|---|---|---|
| **Q7** | Does the deployed savings-vault capital fully sit at the Spark Ethereum ALM, or are there off-ALM strategy contracts we're missing? | Re-run the outbound transfer trace from each vault address over a longer window (e.g. 6 months, not 7 days) and enumerate every contract destination. Cross-check against the address list Spark publishes for their stars-api / allocator registry. **Owner:** settlement-cycle team. **Blocks:** §3.1 (the whole "deployment yield is already captured" premise). |
| **Q8** | Does the Spark ALM's existing venue inventory in `config/spark.yaml` (S26 USDC raw, S27 USDT raw, SparkLend USDT/USDC, sUSDS at ALM, …) accurately price the savings-vault-deployed capital? Or is some of it held at allocator addresses not yet in the venue list? | Once Q7 enumerates allocator destinations, diff that list against current venue config. Any allocator address that *isn't* in the venue list either needs to be added or treated as an out-of-model holding. **Owner:** settlement-cycle team. **Depends on:** Q7. **Blocks:** trust in the headline VSR-only adjustment. |
| **Q9** | Does the Dune `borrow_cost` (VSR) accrue on `total_amount` (all principal) or only `deployed_amount`? Section 3.3 assumes the former. | Verify against either (a) the Dune table's source SQL if available, or (b) by reconciling pps growth on the vault contract against `(1 + borrow_cost / 365)^t × principal` integrated over a calibration window. **Owner:** settlement-cycle team (can be done independently). Magnitude impact: small today (spUSDC 3.3% idle, spUSDT 0.8% idle), but worth pinning. |
| **Q3** | Is Dune `apr` net of vault performance fees? | Not blocking §3.3 (we don't consume `apr` in the headline). Becomes load-bearing in §5.2 reconciliation — if that diverges, this is the first thing to check. **Owner:** Spark, eventually; we can defer until reconciliation actually drifts. |

### 7.2 Resolved (2026-06-01)

| # | Question | Answer |
|---|---|---|
| Q1 | Agent rate applicable to vault-held sUSDS | 20 bps. Moot for the new model — vault doesn't hold sUSDS directly (§2.1). |
| Q2 | Allocator-Vault BR attribution per-vault | BR already in `cum_debt`; do not double-charge. Per-vault attribution not needed. |
| Q4 | Does `holding_amount` accrue VSR or only `deployed_amount`? | Provisionally: VSR accrues on `total_amount`. Re-verification tracked as Q9 above. |
| Q5 | spUSDC zero BR + idle composition + allocation venues | Vault holds only underlying (USDC/USDT/PYUSD). Dominant deployment destination is the Spark Ethereum ALM ($28M USDC / $178M USDT in a 7-day window). "Zero BR for spUSDC" is correct at the vault level; downstream USDS draws are correctly attributed through `cum_debt`. |
| Q6 | spETH scope | Out of scope; separate PRD if/when needed. |

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
- **Avalanche spUSDC (S60)** — small ($37M); same model, deferred per
  §5.3.
- **Performance attribution between Spark and depositors at the per-day
  granularity** — the headline number captures the netted P&L; we
  don't try to reconstruct *which* deployment leg outperformed.

---

## 10. Success criteria

1. S56, S57, S59 venues produce non-zero (negative) `vsr_liability`
   contributions in Spark's monthly settlement breakdown.
2. Per-vault internal consistency checks (§6.1) pass.
3. Closed-form reconciliation (§5.2) lands within 1% — or the gap is
   characterised and the model is corrected accordingly.
4. Headline `prime_agent_revenue` for Spark decreases by the expected
   VSR liability magnitude (~$5M/month at current TVL/VSR).
5. Q7, Q8, Q9 resolved before merging to main.

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
