# Spark Savings V2 — per-vault economic view (SPARK 2026-03)

Per `docs/spark/PRD_savings_vaults.md` §5.2. **Display-only.** Contamination handling: (1) Cat A par-stable venues whose yield comes from `external_alm_sources` sweeps (S26 USDC raw → Anchorage, S28 PYUSD raw → PayPal) are excluded from the mapping in `config/spark.yaml`. (2) The remaining lending venues are weighted by `vault_share = vault_TVL_avg / Σ venue_TVL_avg` to scale out the USDS-minted-via-Allocator co-tenant capital.

## Summary

| Vault | Underlying | TVL avg | Share | VSR liability | VSR APY | Pipeline yield (raw) | Yield (weighted) | apr_eff (weighted) | Net (weighted) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S56 | USDC | $372,280,466.38 | 100.000% | $1,212,564.10 | 3.835% | $463,485.42 | $463,485.42 | 1.466% | -$749,078.69 |
| S57 | USDT | $627,939,145.38 | 91.801% | $1,295,372.43 | 2.429% | $831,112.77 | $762,970.46 | 1.431% | -$532,401.97 |
| S59 | PYUSD | $299,652.64 | 0.150% | $1,245.29 | 4.893% | $80,842.17 | $121.12 | 0.476% | -$1,124.17 |
| S60 | USDC | $113,238,553.72 | 100.000% | $421,343.62 | 4.381% | $19,310.41 | $19,310.41 | 0.201% | -$402,033.21 |
| **Σ** | — | — | — | $2,930,525.46 | — | — | $1,245,887.41 | — | -$1,684,638.04 |

## S56 — Spark Savings V2 — spUSDC vault

Underlying **USDC**, period 31 days, TVL SoM $287,780,830.42 → EoM $456,780,102.34 (avg $372,280,466.38).

- **VSR liability (Phase A — exact):** $1,212,564.10  → effective rate 3.835% APY
- **Mapped yield venues TVL avg:** $185,112,553.40  → vault_share = 100.000%
- **Pipeline yield (raw, all co-tenants):** $463,485.42  → implied rate 1.466% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $463,485.42 → implied rate 1.466% APY
- **Implied Spark spread (weighted):** -2.369% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$749,078.69

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S2 | Spark USDC (SparkLend spToken) | $0.00 |
| S8 | Aave Ethereum USDC (aToken) | $0.00 |
| S10 | Spark Blue Chip USDC Vault (Morpho) | $4,849.50 |
| S14 | Maple syrupUSDC (ERC-4626) | $415,095.01 |
| S18 | Arkis Spark Prime USDC 1 (ERC-4626) | $43,540.91 |

## S57 — Spark Savings V2 — spUSDT vault

Underlying **USDT**, period 31 days, TVL SoM $387,977,397.42 → EoM $867,900,893.34 (avg $627,939,145.38).

- **VSR liability (Phase A — exact):** $1,295,372.43  → effective rate 2.429% APY
- **Mapped yield venues TVL avg:** $684,021,552.95  → vault_share = 91.801%
- **Pipeline yield (raw, all co-tenants):** $831,112.77  → implied rate 1.558% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $762,970.46 → implied rate 1.431% APY
- **Implied Spark spread (weighted):** -0.998% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$532,401.97

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S3 | Spark USDT (SparkLend spToken) | $575,230.70 |
| S9 | Aave Ethereum USDT (aToken) | $0.00 |
| S11 | Spark Blue Chip USDT Vault (Morpho V2) | $3.48 |
| S15 | Maple syrupUSDT (ERC-4626) | $172,403.07 |
| S24 | Spark.fi USDT Reserve Curve (sUSDS/USDT) | $83,475.52 |
| S27 | USDT raw (ALM idle — $442M as of 2026-04) | -$0.00 |

## S59 — Spark Savings V2 — spPYUSD vault

Underlying **PYUSD**, period 31 days, TVL SoM $94,443.72 → EoM $504,861.56 (avg $299,652.64).

- **VSR liability (Phase A — exact):** $1,245.29  → effective rate 4.893% APY
- **Mapped yield venues TVL avg:** $200,000,682.91  → vault_share = 0.150%
- **Pipeline yield (raw, all co-tenants):** $80,842.17  → implied rate 317.652% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $121.12 → implied rate 0.476% APY
- **Implied Spark spread (weighted):** -4.417% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$1,124.17

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S5 | Spark PYUSD (SparkLend spToken) | $71,459.45 |
| S25 | Spark.fi PYUSD Reserve Curve (PYUSD/USDS) | $9,382.72 |

## S60 — Spark Savings V2 — spUSDC vault (Avalanche-C, CREATE2 same address)

Underlying **USDC**, period 31 days, TVL SoM $152,857,139.20 → EoM $73,619,968.24 (avg $113,238,553.72).

- **VSR liability (Phase A — exact):** $421,343.62  → effective rate 4.381% APY
- **Mapped yield venues TVL avg:** $10,000,138.20  → vault_share = 100.000%
- **Pipeline yield (raw, all co-tenants):** $19,310.41  → implied rate 0.201% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $19,310.41 → implied rate 0.201% APY
- **Implied Spark spread (weighted):** -4.180% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$402,033.21

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S55 | USDC raw (Avalanche-C — ALM idle) | -$0.00 |
| S54 | Aave Avalanche USDC (aAvaUSDC) | $19,310.41 |

