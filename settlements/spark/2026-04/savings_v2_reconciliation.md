# Spark Savings V2 — per-vault economic view (SPARK 2026-04)

Per `docs/spark/PRD_savings_vaults.md` §5.2. **Display-only.** Contamination handling: (1) Cat A par-stable venues whose yield comes from `external_alm_sources` sweeps (S26 USDC raw → Anchorage, S28 PYUSD raw → PayPal) are excluded from the mapping in `config/spark.yaml`. (2) The remaining lending venues are weighted by `vault_share = vault_TVL_avg / Σ venue_TVL_avg` to scale out the USDS-minted-via-Allocator co-tenant capital.

**Note — sUSDS POL Sky-revenue reductions (this period):**
- `pol_agent_rate` (S32 only, +20bps over SSR): $223,871.10
- `susds_pol_ssr_credit` (every sky_savings_token Cat B venue: S32 + L2 proxies, SSR rate): $5,096,921.84
- **Combined sUSDS POL sky_revenue reduction: $5,320,792.94**

These reduce what Spark owes Sky on sUSDS POL — they make the SSR-via-index offset against the BR charge explicit in the cash-flow accounting (otherwise the SSR appreciation lives only in `value_eom` growth). The pooled sUSDS POL at the Spark ETH ALM (S32) is funded by both USDS-via-Allocator and savings-vault deposits swapped through PSM3, so these credits are NOT attributed to any single vault in the per-vault table below — surfacing them here for context so the reader can compute Spark's all-in position on savings vaults as `net_spread_weighted_total + pol_agent_rate_total + susds_pol_ssr_credit_total`.

## Summary

| Vault | Underlying | TVL avg | Share | VSR liability | VSR APY | Pipeline yield (raw) | Yield (weighted) | apr_eff (weighted) | Net (weighted) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S56 | USDC | $515,056,129.94 | 100.000% | $1,302,075.64 | 3.076% | $200,693.42 | $200,693.42 | 0.474% | -$1,101,382.22 |
| S57 | USDT | $1,001,302,062.18 | 88.306% | $2,183,895.67 | 2.654% | $1,066,918.41 | $942,157.90 | 1.145% | -$1,241,737.77 |
| S59 | PYUSD | $785,710.72 | 0.393% | $2,803.26 | 4.341% | $55,854.49 | $219.43 | 0.340% | -$2,583.83 |
| S60 | USDC | $55,379,392.84 | 100.000% | $175,138.71 | 3.848% | $0.00 | $0.00 | 0.000% | -$175,138.71 |
| **Σ** | — | — | — | $3,663,913.27 | — | — | $1,143,070.74 | — | -$2,520,842.53 |

## S56 — Spark Savings V2 — spUSDC vault

Underlying **USDC**, period 30 days, TVL SoM $456,780,102.34 → EoM $573,332,157.53 (avg $515,056,129.94).

- **VSR liability (Phase A — exact):** $1,302,075.64  → effective rate 3.076% APY
- **Mapped yield venues TVL avg:** $113,206,742.71  → vault_share = 100.000%
- **Pipeline yield (raw, all co-tenants):** $200,693.42  → implied rate 0.474% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $200,693.42 → implied rate 0.474% APY
- **Implied Spark spread (weighted):** -2.602% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$1,101,382.22

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S2 | Spark USDC (SparkLend spToken) | $125.38 |
| S8 | Aave Ethereum USDC (aToken) | $0.00 |
| S10 | Spark Blue Chip USDC Vault (Morpho) | $9,546.42 |
| S14 | Maple syrupUSDC (ERC-4626) | $172,735.43 |
| S18 | Arkis Spark Prime USDC 1 (ERC-4626) | $18,286.18 |

## S57 — Spark Savings V2 — spUSDT vault

Underlying **USDT**, period 30 days, TVL SoM $867,900,893.34 → EoM $1,134,703,231.02 (avg $1,001,302,062.18).

- **VSR liability (Phase A — exact):** $2,183,895.67  → effective rate 2.654% APY
- **Mapped yield venues TVL avg:** $1,133,894,441.78  → vault_share = 88.306%
- **Pipeline yield (raw, all co-tenants):** $1,066,918.41  → implied rate 1.296% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $942,157.90 → implied rate 1.145% APY
- **Implied Spark spread (weighted):** -1.509% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$1,241,737.77

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S3 | Spark USDT (SparkLend spToken) | $577,512.14 |
| S9 | Aave Ethereum USDT (aToken) | $0.00 |
| S11 | Spark Blue Chip USDT Vault (Morpho V2) | $168,816.64 |
| S15 | Maple syrupUSDT (ERC-4626) | $204,786.96 |
| S24 | Spark.fi USDT Reserve Curve (sUSDS/USDT) | $115,802.67 |
| S27 | USDT raw (ALM idle — $442M as of 2026-04) | -$0.00 |

## S59 — Spark Savings V2 — spPYUSD vault

Underlying **PYUSD**, period 30 days, TVL SoM $504,861.56 → EoM $1,066,559.88 (avg $785,710.72).

- **VSR liability (Phase A — exact):** $2,803.26  → effective rate 4.341% APY
- **Mapped yield venues TVL avg:** $200,000,228.89  → vault_share = 0.393%
- **Pipeline yield (raw, all co-tenants):** $55,854.49  → implied rate 86.490% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $219.43 → implied rate 0.340% APY
- **Implied Spark spread (weighted):** -4.001% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$2,583.83

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S5 | Spark PYUSD (SparkLend spToken) | $46,285.48 |
| S25 | Spark.fi PYUSD Reserve Curve (PYUSD/USDS) | $9,569.01 |

## S60 — Spark Savings V2 — spUSDC vault (Avalanche-C, CREATE2 same address)

Underlying **USDC**, period 30 days, TVL SoM $73,619,968.24 → EoM $37,138,817.43 (avg $55,379,392.84).

- **VSR liability (Phase A — exact):** $175,138.71  → effective rate 3.848% APY
- **Mapped yield venues TVL avg:** $5,001,316.64  → vault_share = 100.000%
- **Pipeline yield (raw, all co-tenants):** $0.00  → implied rate 0.000% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $0.00 → implied rate 0.000% APY
- **Implied Spark spread (weighted):** -3.848% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$175,138.71

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S55 | USDC raw (Avalanche-C — ALM idle) | $0.00 |
| S54 | Aave Avalanche USDC (aAvaUSDC) | $0.00 |

