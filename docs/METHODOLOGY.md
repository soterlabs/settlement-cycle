# Settlement Methodology

Reference for how the four output figures are computed. Applies to all prime agents (OBEX, Grove, Spark, Skybase, …).

---

## 1. `prime_agent_revenue`

**What it is:** The yield the prime's positions generated this month, counting only the prime's share (i.e. after Sky's SDE slice is removed).

**Formula — per venue:**

```
actual_revenue = (value_eom − value_som) − period_inflow
```

- `value_eom / value_som` = token balance × unit price at the EoM / SoM pin block
- `period_inflow` = net capital moved into the venue during the period (ALM → venue transfers), priced at the time of transfer. Strips out capital movements so only yield remains.
- This is a mark-to-market gain, not a NAV-vs-debt comparison. `cum_debt` plays no role here.

**SDE split (Sky Direct Exposure venues):**

```
sd_revenue    = actual_revenue × sd_share   → to Sky
prime_revenue = actual_revenue × (1 − sd_share)  → to prime
```

- `kind = fixed`: `sd_share = 1` (e.g. JTRSY, BUIDL — all yield to Sky)
- `kind = capped`: `sd_share = min(cap_usd, value_som) / value_som` (e.g. JAAA capped at a dollar limit)
- `sd_share` is locked at SoM for the whole month

```
prime_agent_revenue = Σ_venues prime_revenue
```

Non-SDE venues contribute their full `actual_revenue`. SDE venues contribute only `(1 − sd_share)` of it.

### Special case — all held sUSDS (`sky_savings_token: true` venues)

For any venue where the prime holds **sUSDS** (the Sky Savings vault token or a per-chain canonical wrapper), the standard MtM formula overcounts Prime Revenue. The prime physically receives the SSR appreciation through the sUSDS share price. Crediting that same appreciation again as Prime Revenue in the settlement model double-counts — the total economic outcome would be `SSR × V` (actual token gain) `+ SSR × V` (model credit) `− BR × V` (Sky charge) = `(2×SSR − BR) × V`, a positive overcredit of ~3.7%/yr, when the intent is economic neutrality (net = 0).

This applies to **all direct sUSDS holdings** — raw ALM/POL positions today, and (when implemented) sUSDS legs inside LP tokens. It is governed by the `sky_savings_token: true` flag in the prime YAML config rather than inferred from the token address, so coverage is explicit and per-prime.

**Correct treatment — 30 bps spread only:**

```
prime_revenue_sUSDS = value_som × ((1 + 0.30%)^(1/365) − 1) × n_days
```

- `value_som` = shares × `convertToAssets(som_block)` — the USDS-equivalent at SoM
- `0.30%` = BR − SSR = the spread the prime earns above the savings rate
- `n_days` = calendar days in the settlement period

This matches "Method 2" from the accounting equivalence below:

| | Method 1 (exact) | Method 2 (simplification, implemented) |
|---|---|---|
| Sky Revenue | SSR × V | BR × V |
| Prime Revenue | 0 | (BR − SSR) × V = 30bps × V |
| **Net to Prime** | **−SSR × V** | **30bps × V − BR × V = −SSR × V** ✓ |

Both formulations give the same economic outcome (Sky earns SSR net; prime pays SSR net). Method 2 re-uses the existing `utilized`-based BR charge and adds the 30bps spread as prime revenue.

**Current `sky_savings_token` venues (Spark):**

| Venue | Chain | Type |
|---|---|---|
| S32 | Ethereum | Raw sUSDS POL at ALM — `pricing_category: B` |
| S37 | Base | sUSDS proxy POL — `pricing_category: B` |
| S43 | Arbitrum | sUSDS proxy POL — `pricing_category: B` |
| S47 | Optimism | sUSDS proxy POL — `pricing_category: B` |
| S51 | Unichain | sUSDS proxy POL — `pricing_category: B` |
| S24 | Ethereum | sUSDS leg of sUSDS/USDT Curve pool — `curve_idle_usds.sky_savings_token: true` |

For raw sUSDS venues (Cat B): `actual_revenue_override = value_som × 30bps_daily × n_days`.

For LP-embedded sUSDS venues (Curve `curve_idle_usds`): `spread_d = prime_sUSDS_value_d × 30bps_daily`, summed across the period and added to `prime_agent_revenue` outside the per-venue loop.

---

## 2. `agent_rate`

**What it is:** What Sky pays the prime for idle USDS/sUSDS sitting in the subproxy (a liquidity-provision fee).

```
agent_rate = Σ_days  subproxy_usds   × ((1 + SSR + 0.20%)^(1/365) − 1)
           + Σ_days  subproxy_susds  × ((1 + 0.20%)^(1/365) − 1)
```

- USDS earns `SSR + 20bps` (full agent rate)
- sUSDS earns only `20bps` — SSR already accrues via the token's index; applying it again would double-count
- `subproxy_susds` is the **cost-basis principal** (`shares × entry_pps`), not the current value
- Subproxy balances are **not** deducted from `utilized` — they represent treasury/risk capital, not idle ilk-funded USDS

---

## 3. `sky_revenue`

**What it is:** Sky's total claim for the period. Two components:

```
sky_revenue = sky_rev_br + sde_revenue
```

**`sky_rev_br`** — base-rate interest on deployed (utilized) USDS:

```
sky_rev_br = Σ_days  max(utilized_d, 0) × ((1 + effective_br_d)^(1/365) − 1)
```

where `effective_br_d` is the subsidised borrow rate for the day (= `SSR + 30bps` at full rate; steps down toward `ref_rate + 30bps` over the 24-month subsidy programme).

**`sde_revenue`** — actual yield from SDE positions (from the `sd_revenue` fields above):

```
sde_revenue = Σ_venues sd_revenue
```

**`utilized`** — the portion of drawn USDS that is actually deployed (not idle, not SDE):

```
utilized_d = cum_debt_d
           − alm_proxy_usds_d         ← idle raw USDS at the ALM proxy
           − psm_usds_d               ← idle USDS deposited in PSM3 (any chain)
           − sde_asset_value_d        ← daily NAV of SDE positions (BUIDL, JTRSY, JAAA-cap…)
           − curve_idle_usds_d        ← USDS leg of Curve LP pools (par-stable coins only)
           − lending_idle_usds_d      ← prime's proportional idle underlying in lending pools
```

**Notes on each deduction:**

`alm_proxy_usds` — raw USDS sitting idle at the ALM proxy address; subtracted because it is not earning anything and should not be billed the borrow rate.

`psm_usds` — USDS deposited into PSM3 (Sky's Peg Stability Module); behaves as idle USDS for the prime's purposes.

`sde_asset_value` — SDE positions pay Sky directly via `sde_revenue`; charging BR on top would double-bill.

`curve_idle_usds` — **Par-stable coin legs only** (USDS, USDC at $1). For each venue with `curve_idle_usds` config, prime's proportional USDS share of the pool reserve is computed daily via RPC:

```
curve_idle_usds_d = Σ_venues  (alm_lp_d / pool_total_supply_d) × coin_reserve_d
```

Yield-bearing coin legs (e.g. sUSDS in an sUSDS/USDT pool) are **not** subtracted from utilized. The sUSDS balance is fetched at each snapshot for a future Prime Revenue addition but does not reduce the borrow-rate base.

`lending_idle_usds` — unborrowed underlying sitting inside SparkLend / Aave pools where the prime holds the corresponding spToken / aToken:

```
lending_idle_usds_d = Σ_venues  (balanceOf(ALM, spToken_d) / totalSupply(spToken_d))
                               × balanceOf(spToken_contract, underlying_d)
```

Enabled per-venue via `lending_idle_usds: true` in the prime YAML config (Cat C / D venues only).

**`cum_debt`** — source of truth for total drawn USDS. Derived from the Sky Vat on-chain:

- Scans all `frob` calls (selector `0x76088703`) to the Vat (`0x35D1…492B`) filtered to the prime's `ilk_bytes32`
- Each call carries a signed `dart` (change in normalized debt, 1e18 units) at calldata offset 165
- `cum_debt = Σ dart` from `prime.start_date` through the EoM pin block
- Matches `Vat.ilks[ilk].Art × rate` — the total outstanding USDS drawn against that ilk
- Ilk-level, not per-vault: if the prime's ilk has multiple vaults or subproxies, `cum_debt` captures the aggregate

---

## 4. `monthly_pnl` — known issue ⚠️

```
monthly_pnl = prime_agent_revenue + agent_rate − sky_revenue
            = (total_yield − SDE) + agent_rate − (sky_rev_br + SDE)
            = total_yield + agent_rate − sky_rev_br − 2 × SDE
```

**SDE is double-counted.** It is subtracted from `prime_agent_revenue` (prime never earns it) and then added into `sky_revenue` (Sky claims it). The net subtracts it twice, producing a figure that is neither Grove's net profit nor the correct settlement transfer.

The correct net settlement is:

```
net_settlement = prime_agent_revenue + agent_rate − sky_rev_br
               = prime_agent_revenue + agent_rate + sde_revenue − sky_revenue
```

Example — February 2026 Grove: current `monthly_pnl` = −$5.27M; correct net = −$2.05M.

---

## Rate conventions

- All rates use **APY with daily compounding**: `daily_factor = (1 + APY)^(1/365) − 1`
- SSR is tracked daily via SP-BEAM governance calls; see `docs/RULES.md` for history
- Borrow rate = `SSR + 30bps`; agent rate = `SSR + 20bps` (USDS) / `20bps` (sUSDS)
- Subsidised borrow rate ramps from `ref_rate + 30bps` toward full `SSR + 30bps` over 24 months from 2026-01-01; capped at `subsidy.cap_usd` per prime (excess at full rate)
