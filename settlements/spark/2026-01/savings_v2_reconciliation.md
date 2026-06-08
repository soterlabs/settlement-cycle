# Spark Savings V2 — per-vault economic view (SPARK 2026-01)

Per `docs/spark/PRD_savings_vaults.md` §5.2. **Display-only.** The pipeline-yield column is an UPPER BOUND — the mapped venues hold capital from both savings-vault depositors and USDS-minted-via-Allocator-Vault, so attributing 100% of their yield to the vault over-attributes by the Allocator-funded share. Read `apr_eff` as a maximum yield envelope and treat implausibly high values (⚠) as co-tenant contamination flags.

## Summary

| Vault | Underlying | TVL avg | VSR liability | VSR APY | Pipeline yield (max) | apr_eff (max) | Net upper bound |
|---|---|---:|---:|---:|---:|---:|---:|
| S56 | USDC | $211,205,119.73 | $635,707.88 | 3.544% | $1,993,706.17 | 11.114% ⚠ | $1,357,998.29 |
| S57 | USDT | $136,947,474.90 | $549,209.61 | 4.722% | $994,062.63 | 8.547% | $444,853.02 |
| S59 | PYUSD | $153,425.24 | $714.37 | 5.482% | $246,121.49 | 1888.791% ⚠ | $245,407.11 |
| S60 | USDC | $143,035,529.26 | $651,003.18 | 5.359% | $31,596.95 | 0.260% | -$619,406.23 |
| **Σ** | — | — | $1,836,635.04 | — | $3,265,487.24 | — | $1,428,852.20 |

**⚠ implausible apr_eff** — a mapped venue's `actual_revenue` includes yield from non-savings-vault sources (e.g. Anchorage USDC sweeps landing at S26, PayPal PYUSD rewards landing at S28). Reduce the mapping or weight by the vault's share of total ALM underlying.

## S56 — Spark Savings V2 — spUSDC vault

Underlying **USDC**, period 31 days, TVL SoM $215,278,739.04 → EoM $207,131,500.43 (avg $211,205,119.73).

- **VSR liability (Phase A — exact):** $635,707.88  → effective rate 3.544% APY
- **Pipeline yield on mapped venues (upper bound):** $1,993,706.17 → implied rate 11.114% APY ⚠ implausibly high — co-tenant attribution likely
- **Implied Spark spread (upper bound):** 7.571% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = $1,357,998.29

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S26 | USDC raw (ALM idle) | $891,780.00 |
| S2 | Spark USDC (SparkLend spToken) | $145,492.43 |
| S8 | Aave Ethereum USDC (aToken) | $0.00 |
| S10 | Spark Blue Chip USDC Vault (Morpho) | $22,913.63 |
| S14 | Maple syrupUSDC (ERC-4626) | $850,905.46 |
| S18 | Arkis Spark Prime USDC 1 (ERC-4626) | $82,614.64 |

## S57 — Spark Savings V2 — spUSDT vault

Underlying **USDT**, period 31 days, TVL SoM $153,856,441.43 → EoM $120,038,508.36 (avg $136,947,474.90).

- **VSR liability (Phase A — exact):** $549,209.61  → effective rate 4.722% APY
- **Pipeline yield on mapped venues (upper bound):** $994,062.63 → implied rate 8.547% APY
- **Implied Spark spread (upper bound):** 3.825% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = $444,853.02

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S27 | USDT raw (ALM idle — $442M as of 2026-04) | $0.00 |
| S3 | Spark USDT (SparkLend spToken) | $966,565.60 |
| S9 | Aave Ethereum USDT (aToken) | $0.00 |
| S11 | Spark Blue Chip USDT Vault (Morpho V2) | $0.00 |
| S15 | Maple syrupUSDT (ERC-4626) | $0.00 |
| S24 | Spark.fi USDT Reserve Curve (sUSDS/USDT) | $27,497.03 |

## S59 — Spark Savings V2 — spPYUSD vault

Underlying **PYUSD**, period 31 days, TVL SoM $21,463.34 → EoM $285,387.14 (avg $153,425.24).

- **VSR liability (Phase A — exact):** $714.37  → effective rate 5.482% APY
- **Pipeline yield on mapped venues (upper bound):** $246,121.49 → implied rate 1888.791% APY ⚠ implausibly high — co-tenant attribution likely
- **Implied Spark spread (upper bound):** 1883.309% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = $245,407.11

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S28 | PYUSD raw (ALM idle — $677M as of 2026-04) | -$0.00 |
| S5 | Spark PYUSD (SparkLend spToken) | $238,689.83 |
| S25 | Spark.fi PYUSD Reserve Curve (PYUSD/USDS) | $7,431.66 |

## S60 — Spark Savings V2 — spUSDC vault (Avalanche-C, CREATE2 same address)

Underlying **USDC**, period 31 days, TVL SoM $190,414,182.35 → EoM $95,656,876.18 (avg $143,035,529.26).

- **VSR liability (Phase A — exact):** $651,003.18  → effective rate 5.359% APY
- **Pipeline yield on mapped venues (upper bound):** $31,596.95 → implied rate 0.260% APY
- **Implied Spark spread (upper bound):** -5.099% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = -$619,406.23

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S55 | USDC raw (Avalanche-C — ALM idle) | -$0.00 |
| S54 | Aave Avalanche USDC (aAvaUSDC) | $31,596.95 |

