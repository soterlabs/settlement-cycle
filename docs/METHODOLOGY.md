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

- `kind = fixed`: 100 % of `actual_revenue` goes to Sky.
- `kind = capped`: computed daily using end-of-day position snapshots.

For `kind = capped`, the split accumulates day by day:

```
For each day d in [period.start, period.end]:
    sd_share_d  = min(cap_usd, v_d) / v_d   (0 if v_d = 0)
    daily_rev_d = (v_d − v_{d−1}) − inflow_d
    sd_rev_d    = daily_rev_d × sd_share_d

sd_revenue    = Σ_d sd_rev_d                              → to Sky
prime_revenue = actual_revenue − sd_revenue + external_revenue → to prime
```

where `v_d` is the daily end-of-day position value (from the same balance ×
NAV oracle timeseries used for `sde_asset_value`), and `v_0 = value_som`.

`sd_share_avg = sd_revenue / actual_revenue` is reported as a period summary
figure (the weighted-average sky share for the month).

```
prime_agent_revenue = Σ_venues prime_revenue
```

Non-SDE venues contribute their full `actual_revenue`. SDE venues contribute only `actual_revenue − sd_revenue` of it.

### Special case — all held sUSDS (`sky_savings_token: true` venues)

For any venue where the prime holds **sUSDS** (the Sky Savings vault token or a per-chain canonical wrapper), the standard MtM formula overcounts Prime Revenue. The prime physically receives the SSR appreciation through the sUSDS share price. Crediting that same appreciation again as Prime Revenue in the settlement model double-counts — the total economic outcome would be `SSR × V` (actual token gain) `+ SSR × V` (model credit) `− BR × V` (Sky charge) = `(2×SSR − BR) × V`, a positive overcredit of ~3.7%/yr, when the intent is economic neutrality (net = 0).

This applies to **all direct sUSDS holdings** — raw ALM/POL positions today, and (when implemented) sUSDS legs inside LP tokens. It is governed by the `sky_savings_token: true` flag in the prime YAML config rather than inferred from the token address, so coverage is explicit and per-prime.

**Correct treatment — 30 bps spread deducted from Sky Revenue:**

```
sky_revenue_reduction_sUSDS = value_som × ((1 + 0.30%)^(1/365) − 1) × n_days
prime_revenue_sUSDS = 0
```

- `value_som` = shares × `convertToAssets(som_block)` — the USDS-equivalent at SoM (after Savings V2 deduction where applicable)
- `0.30%` = BR − SSR = the spread the prime earns above the savings rate
- `n_days` = calendar days in the settlement period

This is equivalent to Method 1 (exact accounting):

| | Method 1 (exact, **implemented**) | ~~Method 2 (removed)~~ |
|---|---|---|
| Sky Revenue | (BR − 30bps) × V = SSR × V | ~~BR × V~~ |
| Prime Revenue | 0 | ~~(BR − SSR) × V = 30bps × V~~ |
| **Net to Prime** | **0 − SSR × V = −SSR × V** ✓ | ~~30bps × V − BR × V = −SSR × V~~ |

Sky charges full BR on `utilized`, then reduces its invoice by `30bps × value_som × n_days` per sky_savings_token venue. The reduction is surfaced as `susds_spread_reimbursement` — a per-venue column in `venues.csv` and `grove_sheet.csv`, and a headline sub-row in the monthly report.

**Current `sky_savings_token` venues (Spark):**

| Venue | Chain | Type |
|---|---|---|
| S32 | Ethereum | Raw sUSDS POL at ALM — `pricing_category: B` |
| S37 | Base | sUSDS proxy POL — `pricing_category: B` |
| S43 | Arbitrum | sUSDS proxy POL — `pricing_category: B` |
| S47 | Optimism | sUSDS proxy POL — `pricing_category: B` |
| S51 | Unichain | sUSDS proxy POL — `pricing_category: B` |
| S24 | Ethereum | sUSDS leg of sUSDS/USDT Curve pool — `curve_idle_usds.sky_savings_token: true` |

For raw sUSDS venues (Cat B): `susds_spread_reimbursement = value_som × 30bps_daily × n_days` deducted from `sky_revenue`; prime revenue = 0.

For LP-embedded sUSDS venues (Curve `curve_idle_usds`): `spread_d = prime_sUSDS_value_d × 30bps_daily`, summed across the period and added to `prime_agent_revenue` outside the per-venue loop (unchanged — see `curve_susds_spread` in provenance).

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
           − psm_usds_d               ← idle USDS-equivalent at any PSM (any chain, any kind)
           − sde_asset_value_d        ← daily NAV of SDE positions (BUIDL, JTRSY, JAAA-cap…)
           − curve_idle_usds_d        ← USDS leg of Curve LP pools (par-stable coins only)
           − lending_idle_usds_d      ← prime's proportional idle underlying in lending pools
```

**Notes on each deduction:**

`alm_proxy_usds` — raw USDS sitting idle at the ALM proxy address; subtracted because it is not earning anything and should not be billed the borrow rate.

`psm_usds` — USDS-equivalent value the prime has parked at any PSM. Today only one PSM mechanic is tracked:
- **`erc4626_shares`** (Spark PSM3 on Base/Arbitrum/Optimism/Unichain): daily snapshot `convertToAssetValue(shares(alm, b), b)` via RPC. ~$544M USDS-equivalent total for Spark across the 4 L2s as of 2026-05.

Sky's mainnet PSM stack (`DssLitePsm` + `DaiUsds` converter + USDC pocket EOA + `UsdsPsmWrapper`) is non-custodial — primes transit through it as atomic swaps without accumulating balances. No per-prime tracking is needed on Ethereum; this was an earlier `directed_flow` PsmKind which was removed on 2026-05-11 after on-chain verification. See PRD §17.11.

**PSM3 leg-split** (since 2026-05-11): the ERC4626_SHARES path decomposes the total per-day into three legs — USDC + USDS + sUSDS reserves. Each leg is routed differently to keep the prime economically neutral on idle PSM3 capital ("primes should neither pay interest nor earn money for idle USDS / sUSDS"):
- **USDS leg** is subtracted from `utilized` (BR-reimbursed). No SSR is paid on USDS, so just zeroing the BR charge is sufficient for neutrality.
- **USDC leg** is treated as Sky Direct Exposure per Atlas §A.2.3.2.2.3 (added to `sde_asset_value`; Sky takes the actual yield, ≈ $0 for passive reserves; prime is NOT BR-reimbursed on this slice).
- **sUSDS leg** is **not** subtracted from `utilized` (prime pays full BR on this slice). The orchestrator credits back a 30 bps spread × value × n_days as Prime Revenue via `_psm3_susds_spread`. Why: the sUSDS share-price appreciation pays the prime `+SSR` automatically (via `convertToAssetValue` growth); charging full BR `−(SSR + 30 bps)` and crediting `+30 bps` makes the composite `+SSR − (SSR + 30 bps) + 30 bps = 0`. Both sides net to zero. Subtracting sUSDS from `utilized` without crediting back would leave Sky paying SSR with no offset (an unintended subsidy).

Same shape as the rule for sUSDS held inside Curve LP pools (RULES §5).

Apportionment per day: `spark_share_of_pool = convertToAssetValue(spark_shares) / pool_total_usds_eq`, where `pool_total_usds_eq = USDC_reserve + USDS_reserve + sUSDS_reserve × sUSDS_pps`. sUSDS pps is read from the Ethereum sUSDS vault (the L2 sUSDS is a 1:1 bridge — verified to 4 decimals).

In both PSM mechanics the timeseries is consumed as a "value-as-of-date" reading on the per-leg cum_X columns, so the meaning is consistent at the consumer despite different mechanics inside.

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
