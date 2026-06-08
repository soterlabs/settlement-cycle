# Spark Savings V2 — per-vault economic view (SPARK 2026-01)

Per `docs/spark/PRD_savings_vaults.md` §5.2. **Display-only.** Contamination handling: (1) Cat A par-stable venues whose yield comes from `external_alm_sources` sweeps (S26 USDC raw → Anchorage, S28 PYUSD raw → PayPal) are excluded from the mapping in `config/spark.yaml`. (2) The remaining lending venues are weighted by `vault_share = vault_TVL_avg / Σ venue_TVL_avg` to scale out the USDS-minted-via-Allocator co-tenant capital.

**Note — S32 POL agent rate income (this period): $87,501.45.** This is a prime-level income line earned on the pooled sUSDS POL at the Spark ETH ALM (funded by both USDS-via-Allocator and savings-vault deposits swapped through PSM3). It's already in `prime_agent_revenue` and is NOT attributed to any single vault in the per-vault table below — surfacing it here for context so the reader can compute Spark's all-in position on savings vaults as `net_spread_weighted_total + pol_agent_rate_total`.

## Summary

| Vault | Underlying | TVL avg | Share | VSR liability | VSR APY | Pipeline yield (raw) | Yield (weighted) | apr_eff (weighted) | Net (weighted) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S56 | USDC | $211,205,119.73 | 79.991% | $635,707.88 | 3.544% | $1,101,926.17 | $881,445.21 | 4.914% | $245,737.33 |
| S57 | USDT | $136,947,474.90 | 41.482% | $549,209.61 | 4.722% | $994,062.63 | $412,355.16 | 3.545% | -$136,854.45 |
| S59 | PYUSD | $153,425.24 | 0.069% | $714.37 | 5.482% | $246,121.49 | $169.95 | 1.304% | -$544.43 |
| S60 | USDC | $143,035,529.26 | 100.000% | $651,003.18 | 5.359% | $31,596.95 | $31,596.95 | 0.260% | -$619,406.23 |
| **Σ** | — | — | — | $1,836,635.04 | — | — | $1,325,567.26 | — | -$511,067.78 |

## S56 — Spark Savings V2 — spUSDC vault

Underlying **USDC**, period 31 days, TVL SoM $215,278,739.04 → EoM $207,131,500.43 (avg $211,205,119.73).

- **VSR liability (Phase A — exact):** $635,707.88  → effective rate 3.544% APY
- **Mapped yield venues TVL avg:** $264,035,071.86  → vault_share = 79.991%
- **Pipeline yield (raw, all co-tenants):** $1,101,926.17  → implied rate 6.143% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $881,445.21 → implied rate 4.914% APY
- **Implied Spark spread (weighted):** 1.370% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = $245,737.33

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S2 | Spark USDC (SparkLend spToken) | $145,492.43 |
| S8 | Aave Ethereum USDC (aToken) | $0.00 |
| S10 | Spark Blue Chip USDC Vault (Morpho) | $22,913.63 |
| S14 | Maple syrupUSDC (ERC-4626) | $850,905.46 |
| S18 | Arkis Spark Prime USDC 1 (ERC-4626) | $82,614.64 |

## S57 — Spark Savings V2 — spUSDT vault

Underlying **USDT**, period 31 days, TVL SoM $153,856,441.43 → EoM $120,038,508.36 (avg $136,947,474.90).

- **VSR liability (Phase A — exact):** $549,209.61  → effective rate 4.722% APY
- **Mapped yield venues TVL avg:** $330,138,630.77  → vault_share = 41.482%
- **Pipeline yield (raw, all co-tenants):** $994,062.63  → implied rate 8.547% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $412,355.16 → implied rate 3.545% APY
- **Implied Spark spread (weighted):** -1.177% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$136,854.45

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S3 | Spark USDT (SparkLend spToken) | $966,565.60 |
| S9 | Aave Ethereum USDT (aToken) | $0.00 |
| S11 | Spark Blue Chip USDT Vault (Morpho V2) | $0.00 |
| S15 | Maple syrupUSDT (ERC-4626) | $0.00 |
| S24 | Spark.fi USDT Reserve Curve (sUSDS/USDT) | $27,497.03 |
| S27 | USDT raw (ALM idle — $442M as of 2026-04) | $0.00 |

## S59 — Spark Savings V2 — spPYUSD vault

Underlying **PYUSD**, period 31 days, TVL SoM $21,463.34 → EoM $285,387.14 (avg $153,425.24).

- **VSR liability (Phase A — exact):** $714.37  → effective rate 5.482% APY
- **Mapped yield venues TVL avg:** $222,191,702.14  → vault_share = 0.069%
- **Pipeline yield (raw, all co-tenants):** $246,121.49  → implied rate 1888.791% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $169.95 → implied rate 1.304% APY
- **Implied Spark spread (weighted):** -4.178% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$544.43

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S5 | Spark PYUSD (SparkLend spToken) | $238,689.83 |
| S25 | Spark.fi PYUSD Reserve Curve (PYUSD/USDS) | $7,431.66 |

## S60 — Spark Savings V2 — spUSDC vault (Avalanche-C, CREATE2 same address)

Underlying **USDC**, period 31 days, TVL SoM $190,414,182.35 → EoM $95,656,876.18 (avg $143,035,529.26).

- **VSR liability (Phase A — exact):** $651,003.18  → effective rate 5.359% APY
- **Mapped yield venues TVL avg:** $10,000,701.55  → vault_share = 100.000%
- **Pipeline yield (raw, all co-tenants):** $31,596.95  → implied rate 0.260% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $31,596.95 → implied rate 0.260% APY
- **Implied Spark spread (weighted):** -5.099% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$619,406.23

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S55 | USDC raw (Avalanche-C — ALM idle) | -$0.00 |
| S54 | Aave Avalanche USDC (aAvaUSDC) | $31,596.95 |

