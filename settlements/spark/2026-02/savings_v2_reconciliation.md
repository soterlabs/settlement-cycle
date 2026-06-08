# Spark Savings V2 — per-vault economic view (SPARK 2026-02)

Per `docs/spark/PRD_savings_vaults.md` §5.2. **Display-only.** Contamination handling: (1) Cat A par-stable venues whose yield comes from `external_alm_sources` sweeps (S26 USDC raw → Anchorage, S28 PYUSD raw → PayPal) are excluded from the mapping in `config/spark.yaml`. (2) The remaining lending venues are weighted by `vault_share = vault_TVL_avg / Σ venue_TVL_avg` to scale out the USDS-minted-via-Allocator co-tenant capital.

**Note — S32 POL agent rate income (this period): $90,940.64.** This is a prime-level income line earned on the pooled sUSDS POL at the Spark ETH ALM (funded by both USDS-via-Allocator and savings-vault deposits swapped through PSM3). It's already in `prime_agent_revenue` and is NOT attributed to any single vault in the per-vault table below — surfacing it here for context so the reader can compute Spark's all-in position on savings vaults as `net_spread_weighted_total + pol_agent_rate_total`.

## Summary

| Vault | Underlying | TVL avg | Share | VSR liability | VSR APY | Pipeline yield (raw) | Yield (weighted) | apr_eff (weighted) | Net (weighted) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S56 | USDC | $247,456,165.43 | 100.000% | $627,039.83 | 3.303% | $746,948.09 | $746,948.09 | 3.935% | $119,908.26 |
| S57 | USDT | $254,007,952.89 | 71.467% | $742,151.61 | 3.809% | $548,642.84 | $392,097.85 | 2.012% | -$350,053.75 |
| S59 | PYUSD | $189,915.43 | 0.095% | $574.47 | 3.943% | $74,139.64 | $70.40 | 0.483% | -$504.07 |
| S60 | USDC | $124,257,007.69 | 100.000% | $387,026.44 | 4.060% | $19,422.10 | $19,422.10 | 0.204% | -$367,604.34 |
| **Σ** | — | — | — | $1,756,792.35 | — | — | $1,158,538.44 | — | -$598,253.91 |

## S56 — Spark Savings V2 — spUSDC vault

Underlying **USDC**, period 28 days, TVL SoM $207,131,500.43 → EoM $287,780,830.42 (avg $247,456,165.43).

- **VSR liability (Phase A — exact):** $627,039.83  → effective rate 3.303% APY
- **Mapped yield venues TVL avg:** $209,397,303.25  → vault_share = 100.000%
- **Pipeline yield (raw, all co-tenants):** $746,948.09  → implied rate 3.935% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $746,948.09 → implied rate 3.935% APY
- **Implied Spark spread (weighted):** 0.632% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = $119,908.26

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S2 | Spark USDC (SparkLend spToken) | $103,808.62 |
| S8 | Aave Ethereum USDC (aToken) | $0.00 |
| S10 | Spark Blue Chip USDC Vault (Morpho) | $2,260.87 |
| S14 | Maple syrupUSDC (ERC-4626) | $582,414.44 |
| S18 | Arkis Spark Prime USDC 1 (ERC-4626) | $58,464.16 |

## S57 — Spark Savings V2 — spUSDT vault

Underlying **USDT**, period 28 days, TVL SoM $120,038,508.36 → EoM $387,977,397.42 (avg $254,007,952.89).

- **VSR liability (Phase A — exact):** $742,151.61  → effective rate 3.809% APY
- **Mapped yield venues TVL avg:** $355,420,578.33  → vault_share = 71.467%
- **Pipeline yield (raw, all co-tenants):** $548,642.84  → implied rate 2.816% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $392,097.85 → implied rate 2.012% APY
- **Implied Spark spread (weighted):** -1.796% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$350,053.75

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S3 | Spark USDT (SparkLend spToken) | $412,444.80 |
| S9 | Aave Ethereum USDT (aToken) | $0.00 |
| S11 | Spark Blue Chip USDT Vault (Morpho V2) | $0.00 |
| S15 | Maple syrupUSDT (ERC-4626) | $101,946.98 |
| S24 | Spark.fi USDT Reserve Curve (sUSDS/USDT) | $34,251.06 |
| S27 | USDT raw (ALM idle — $442M as of 2026-04) | -$0.00 |

## S59 — Spark Savings V2 — spPYUSD vault

Underlying **PYUSD**, period 28 days, TVL SoM $285,387.14 → EoM $94,443.72 (avg $189,915.43).

- **VSR liability (Phase A — exact):** $574.47  → effective rate 3.943% APY
- **Mapped yield venues TVL avg:** $199,998,430.96  → vault_share = 0.095%
- **Pipeline yield (raw, all co-tenants):** $74,139.64  → implied rate 508.891% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $70.40 → implied rate 0.483% APY
- **Implied Spark spread (weighted):** -3.460% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$504.07

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S5 | Spark PYUSD (SparkLend spToken) | $61,312.99 |
| S25 | Spark.fi PYUSD Reserve Curve (PYUSD/USDS) | $12,826.65 |

## S60 — Spark Savings V2 — spUSDC vault (Avalanche-C, CREATE2 same address)

Underlying **USDC**, period 28 days, TVL SoM $95,656,876.18 → EoM $152,857,139.20 (avg $124,257,007.69).

- **VSR liability (Phase A — exact):** $387,026.44  → effective rate 4.060% APY
- **Mapped yield venues TVL avg:** $10,000,554.27  → vault_share = 100.000%
- **Pipeline yield (raw, all co-tenants):** $19,422.10  → implied rate 0.204% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $19,422.10 → implied rate 0.204% APY
- **Implied Spark spread (weighted):** -3.857% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$367,604.34

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S55 | USDC raw (Avalanche-C — ALM idle) | -$0.00 |
| S54 | Aave Avalanche USDC (aAvaUSDC) | $19,422.10 |

