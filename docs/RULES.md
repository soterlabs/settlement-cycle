# MSC Revenue Calculation Rules

Reference rules for computing prime agent revenues in the Sky ecosystem. These rules apply to all agents (OBEX, Skybase, Grove, Spark). Established after reconciling Dune queries against MSC settlement posts.

## Rule 1: The Base Rate is NOMINAL (APR); SSR is an APY and is converted

**Revised 2026-09-01** (see PRD §17.13). The two rates in `BR = SSR + spread`
are quoted in different units, and mixing them was the root of a long-running
discrepancy:

* **SSR is an APY** — it compounds per-second into the sUSDS index on-chain.
* **The spread (and the subsidy reference rate) are nominal APRs.**

So SSR is converted to its nominal equivalent before the two are added:

```
SSR_apr        = 12 × [(1 + SSR_apy)^(1/12) - 1]      # n=12: the settlement cadence
base_apr       = SSR_apr + spread                      # 3.464456% + 0.20% = 3.664456%
daily_interest = D × base_apr / 365                    # NOMINAL — no intra-period compounding
```

Three properties this buys:

1. `BR_apr − SSR_apr − spread = 0` exactly **at the rate level**, which is
   what the additive composition is for. It does *not* make the idle-sUSDS
   legs cancel in dollars: the SSR-appreciation legs credit the APY daily
   factor `(1+SSR)^(1/365)−1` (tracking the index the prime actually holds),
   which sits **0.48 bps/yr below** the `SSR_apr/365` the charge bills. That
   residual is the price of `n = 12` — see the trade-off table in PRD §17.13.
2. The conversion round-trips: `(1 + SSR_apr/12)^12 − 1` returns the SSR APY
   (3.52%) exactly, because the conversion frequency matches the frequency at
   which the charge actually compounds. NB this holds for the *converted leg*,
   not for `base_apr` — compounding `base_apr` monthly gives 3.7266%, which is
   the APY equivalent of the Base Rate, not the SSR.
3. It matches the MSC settlement posts, which have always used `D × rate/365`.

**The charge still compounds — just not inside the period.** Each month's
charge is capitalised into the prime's ilk debt at the MSC (`vat.grab` with
positive `dart`; allocator ilks have `duty = 0` and a frozen `vat.rate`, so
this is the capitalisation mechanism), and `cum_debt` sums frob + grab. The
enlarged principal then pays BR from the settlement day onward, with no code
required.

**Still an APY, deliberately:** the SSR-appreciation legs (PSM3 sUSDS
appreciation, the Curve Case-3b integral, Savings-V2 depositor SSR). Those
model a *physical receipt* — the index really does compound per-second — so
a nominal sum would under-credit what the prime demonstrably received.

*Superseded:* "All calculations use APY with per-second compounding", which
applied `D × [(1+APY)^(1/365) − 1]` to every leg. It made `BR − SSR − spread`
non-zero and billed `ln(1+APY)` over a year instead of the APY.

## Rule 2: Track SSR changes via SP-BEAM

The SSR is adjusted through **SP-BEAM** governance parameter changes. It can change multiple times per month (e.g., Nov–Dec 2025 had 4 changes). Queries must apply the correct SSR for each day, not a single monthly rate.

Source: [Dune query 6953056](https://dune.com/queries/6953056) — reads `file(bytes32("ssr"), uint256)` calls on sUSDS (`0xa3931d...fbD`).

### SSR history (onchain)

| Effective date | SSR (APY) | Borrow rate (SSR+0.30%) | Tx |
|----------------|-----------|-------------------------|----|
| Sep 17, 2024 | 6.25% | 6.55% | `0x2221973...` |
| Oct 7, 2024 | 6.50% | 6.80% | `0x0e0dfb0...` |
| Nov 18, 2024 | 8.51% | 8.81% | `0x789c927...` |
| Nov 30, 2024 | 9.51% | 9.81% | `0x1dd6319...` |
| Dec 8, 2024 | 12.51% | 12.81% | `0xc6807c3...` |
| Feb 10, 2025 | 8.76% | 9.06% | `0x37d1ff4...` |
| Feb 24, 2025 | 6.50% | 6.80% | `0x395e70d...` |
| Mar 24, 2025 | 4.50% | 4.80% | `0xfa915f8...` |
| Aug 4, 2025 | 4.75% | 5.05% | `0xf1c5e50...` |
| Oct 27, 2025 | 4.50% | 4.80% | `0xbca8f5e...` |
| **Nov 7, 2025** | **4.25%** | **4.55%** | `0xa63295c...` |
| **Nov 11, 2025** | **4.50%** | **4.80%** | `0xb951835...` |
| **Dec 2, 2025** | **4.25%** | **4.55%** | `0xac1db72...` |
| **Dec 16, 2025** | **4.00%** | **4.30%** | `0xef4bc6f...` |
| **Mar 9, 2026** | **3.75%** | **4.05%** | `0x9c48c28...` |
| **Apr 22, 2026** | **3.65%** | **3.95%** | `0x0b5cec5...` |
| **May 26, 2026** | **3.60%** | **3.90%** | `0x879f269...` |
| **Jul 23, 2026** | **3.52%** | **3.72%** † | `0x12435f6...` |

† The Jul 23, 2026 Stability Scope change ALSO narrowed the BR − SSR
spread from **30 bps to 20 bps** (borrow rate column is SSR+0.20% from
this row on) and switched the subsidy reference rate from the 3M T-Bill
to **SOFR** (see PRD §17.7 / `config/subsidy_reference_rates.yaml`).
Code: `BASE_RATE_SPREAD_SCHEDULE` in `src/settle/compute/sky_revenue.py`.
Day-granularity convention: the whole UTC day of the change uses the new
values, matching the SSR series (last `file()` call per day wins).

## Rule 3: Track subproxy USDS and sUSDS balances for agent rate calculation

The **agent rate** is the earnings owed to the prime agent on its subproxy's idle holdings:

```
daily_agent_rate = subproxy_usds × [(1 + SSR + 0.20%)^(1/365) - 1]
                 + subproxy_susds × [(1.002)^(1/365) - 1]
```

- **USDS in subproxy** earns **SSR + 0.20% APY**
- **sUSDS in subproxy** earns a flat **0.20% APY** (not SSR-based)
- Balances change when MSC settlements arrive (e.g., Feb 2 +442,327 from MSC #4). These mid-month changes must be accounted for day-by-day.
- The MSC settlement posts appear to use flat SSR (without the +0.20% spread) and APR instead of APY. Both are flagged as discrepancies in `agents/obex/findings/`. The forum posts are **not** the source of truth for the correct rate.

Source: [Dune query 6954383](https://dune.com/queries/6954383) (parameterized).

Subproxy balance histories are tracked per agent — see each agent's README under `agents/`.

## Rule 4: Track Vat debt changes for sky revenue calculation

**Sky revenue** is the interest the prime agent owes to Sky, computed from utilized USDS at the borrow rate:

```
daily_sky_revenue = utilized_usds × [(1 + borrow_rate)^(1/365) - 1]
```

- **Borrow rate = SSR + 0.30%; SSR + 0.20% from 2026-07-23** (dated schedule; a subsidised step-down also applies — see PRD §17.7)
- **Utilized USDS = cum_debt − alm_proxy_usds − psm_usds − sde_asset_value − curve_idle_usds − lending_idle_usds**

  | Term | Description |
  |---|---|
  | `cum_debt` | Vat ilk debt (Σ frob `dart` from prime start → EoM pin block) |
  | `alm_proxy_usds` | Idle raw USDS at the ALM proxy |
  | `psm_usds` | Idle USDS-equivalent at any PSM. Only the **L2 PSM3** (Spark Base / Arbitrum / Optimism / Unichain) is tracked today — Sky's mainnet PSM stack is non-custodial and primes don't accumulate balances there. For PSM3 the basket is split per leg: the **USDS leg** reduces `utilized` directly; the **USDC leg** is added to `sde_asset_value` (Sky Direct Exposure per Atlas §A.2.3.2.2.3 — also excluded from BR base, with actual yield routed to Sky); the **sUSDS leg** stays in the BR base and the prime is credited 30 bps × value × n_days as Prime Revenue (Rule 5 — neutralises the SSR-via-share-price + BR-charge composite). See PRD §17.11. L2 PSM3 holdings ~$544M total for Spark as of 2026-05 (≈ 8% USDC SDE, 20% USDS reimbursed, 72% sUSDS BR-charged-with-30bps-credit-back). |
  | `sde_asset_value` | Daily NAV of Sky Direct Exposure positions (BUIDL, JTRSY, JAAA-cap…); Sky collects their yield directly via `sde_revenue` so charging BR on top would double-bill |
  | `curve_idle_usds` | Prime's proportional USDS in Curve LP pools (par-stable coin leg only; yield-bearing legs such as sUSDS are tracked separately — see Rule 5) |
  | `lending_idle_usds` | Prime's proportional share of unborrowed underlying in SparkLend / Aave pools: `(alm_spToken / totalSupply) × underlying_in_contract` |

- Vat debt changes via frob transactions AND MSC settlement debt minting. Both must be tracked.
- Subproxy USDS and subproxy sUSDS are **not** deducted from utilized. They are treasury/risk capital that does not correspond solely to ilk debt.
- The MSC settlement figures imply a slightly higher effective demand than our "utilized USDS" (~1-2% gap growing over time), possibly due to accumulated Vat rate on the ilk art. This is flagged in findings.

## Rule 5: sUSDS spread — BR−SSR spread (30 bps; 20 bps from 2026-07-23) deducted from Sky Revenue

For sUSDS holdings at the ALM or inside LP pools, crediting the SSR appreciation as Prime Revenue double-counts: the prime already receives SSR through the sUSDS share price, so an additional model credit would yield `(2×SSR − BR) × V > 0` — an overcredit of ~3.7%/yr. The intent is economic neutrality (net = 0).

Governed by the `sky_savings_token` flag in the prime YAML config — set explicitly per venue, not inferred from the token address.

**Raw sUSDS at ALM** (`pricing_category: B` venues, flag at venue level):

- Not deducted from `utilized`.
- Prime Revenue = **0** for these venues.
- `sky_revenue_reduction = value_som × ((1 + 0.30%)^(1/365) − 1) × n_days`
- The prime receives this reimbursement as a **reduction in Sky Revenue** (lower debt to Sky), not as Prime Revenue. Surfaced as `susds_spread_reimbursement` in `venues.csv`.
- `sky_revenue` in the settlement output is already net of all such reductions.

**sUSDS inside Curve LP pools** (`curve_idle_usds.sky_savings_token: true`):

- Not deducted from `utilized`.
- Prime Revenue = **0** for the sUSDS slice.
- `sky_revenue_reduction_d = (alm_lp_d / pool_total_d) × (sUSDS_reserve_d × pps_d) × 30bps_daily`
- Summed across the period and deducted from `sky_revenue` (surfaced as `curve_susds_spread` in provenance, folded into `susds_spread_reimbursement` total).
- `pps_d = convertToAssets(1 share, block_d)` to convert sUSDS→USDS.

Net economic outcome: `SSR × V` (actual token gain) `− sky_revenue_net × share = SSR × V − SSR × V = 0`. Sky earns the net SSR; Prime is economically neutral.

**Exception — demand-side sUSDS (`demand_side_spread: true`):**

S32 (Spark ETH sUSDS POL) collateralises Spark Savings deposits (Savings V2), making it a demand-side position. The 30 bps spread reimbursement is applied via Demand Side Distribution Rewards and is **not** deducted from `sky_revenue` in this settlement report.

- Prime Revenue = **0** (same as all `sky_savings_token` venues).
- `sky_revenue` is **not** reduced — Sky charges full BR on `utilized` with no spread deduction for this venue.
- The sUSDS is **not** subtracted from `utilized`. Even though not every USDS unit has been drawn from the ilk at every moment, keeping the full balance in the BR base correctly accounts for the total debt position. Subtracting it would undercount the prime's actual borrowing.
- The flag `demand_side_spread: true` activates this path; it is independent of `sky_savings_token: true` (both must be set).
