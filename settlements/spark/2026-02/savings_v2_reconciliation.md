# Spark Savings V2 — per-vault economic view (SPARK 2026-02)

Per `docs/spark/PRD_savings_vaults.md` §5.2. **Display-only.** The pipeline-yield column is an UPPER BOUND — the mapped venues hold capital from both savings-vault depositors and USDS-minted-via-Allocator-Vault, so attributing 100% of their yield to the vault over-attributes by the Allocator-funded share. Read `apr_eff` as a maximum yield envelope and treat implausibly high values (⚠) as co-tenant contamination flags.

## Summary

| Vault | Underlying | TVL avg | VSR liability | VSR APY | Pipeline yield (max) | apr_eff (max) | Net upper bound |
|---|---|---:|---:|---:|---:|---:|---:|
| S56 | USDC | $247,456,165.43 | $627,039.83 | 3.303% | $1,638,728.09 | 8.633% | $1,011,688.26 |
| S57 | USDT | $254,007,952.89 | $742,151.61 | 3.809% | $548,642.84 | 2.816% | -$193,508.76 |
| S59 | PYUSD | $189,915.43 | $574.47 | 3.943% | $2,899,139.64 | 19899.571% ⚠ | $2,898,565.17 |
| S60 | USDC | $124,257,007.69 | $387,026.44 | 4.060% | $19,422.10 | 0.204% | -$367,604.34 |
| **Σ** | — | — | $1,756,792.35 | — | $5,105,932.66 | — | $3,349,140.32 |

**⚠ implausible apr_eff** — a mapped venue's `actual_revenue` includes yield from non-savings-vault sources (e.g. Anchorage USDC sweeps landing at S26, PayPal PYUSD rewards landing at S28). Reduce the mapping or weight by the vault's share of total ALM underlying.

## S56 — Spark Savings V2 — spUSDC vault

Underlying **USDC**, period 28 days, TVL SoM $207,131,500.43 → EoM $287,780,830.42 (avg $247,456,165.43).

- **VSR liability (Phase A — exact):** $627,039.83  → effective rate 3.303% APY
- **Pipeline yield on mapped venues (upper bound):** $1,638,728.09 → implied rate 8.633% APY
- **Implied Spark spread (upper bound):** 5.329% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = $1,011,688.26

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S26 | USDC raw (ALM idle) | $891,780.00 |
| S2 | Spark USDC (SparkLend spToken) | $103,808.62 |
| S8 | Aave Ethereum USDC (aToken) | $0.00 |
| S10 | Spark Blue Chip USDC Vault (Morpho) | $2,260.87 |
| S14 | Maple syrupUSDC (ERC-4626) | $582,414.44 |
| S18 | Arkis Spark Prime USDC 1 (ERC-4626) | $58,464.16 |

## S57 — Spark Savings V2 — spUSDT vault

Underlying **USDT**, period 28 days, TVL SoM $120,038,508.36 → EoM $387,977,397.42 (avg $254,007,952.89).

- **VSR liability (Phase A — exact):** $742,151.61  → effective rate 3.809% APY
- **Pipeline yield on mapped venues (upper bound):** $548,642.84 → implied rate 2.816% APY
- **Implied Spark spread (upper bound):** -0.993% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = -$193,508.76

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S27 | USDT raw (ALM idle — $442M as of 2026-04) | -$0.00 |
| S3 | Spark USDT (SparkLend spToken) | $412,444.80 |
| S9 | Aave Ethereum USDT (aToken) | $0.00 |
| S11 | Spark Blue Chip USDT Vault (Morpho V2) | $0.00 |
| S15 | Maple syrupUSDT (ERC-4626) | $101,946.98 |
| S24 | Spark.fi USDT Reserve Curve (sUSDS/USDT) | $34,251.06 |

## S59 — Spark Savings V2 — spPYUSD vault

Underlying **PYUSD**, period 28 days, TVL SoM $285,387.14 → EoM $94,443.72 (avg $189,915.43).

- **VSR liability (Phase A — exact):** $574.47  → effective rate 3.943% APY
- **Pipeline yield on mapped venues (upper bound):** $2,899,139.64 → implied rate 19899.571% APY ⚠ implausibly high — co-tenant attribution likely
- **Implied Spark spread (upper bound):** 19895.628% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = $2,898,565.17

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S28 | PYUSD raw (ALM idle — $677M as of 2026-04) | $2,825,000.00 |
| S5 | Spark PYUSD (SparkLend spToken) | $61,312.99 |
| S25 | Spark.fi PYUSD Reserve Curve (PYUSD/USDS) | $12,826.65 |

## S60 — Spark Savings V2 — spUSDC vault (Avalanche-C, CREATE2 same address)

Underlying **USDC**, period 28 days, TVL SoM $95,656,876.18 → EoM $152,857,139.20 (avg $124,257,007.69).

- **VSR liability (Phase A — exact):** $387,026.44  → effective rate 4.060% APY
- **Pipeline yield on mapped venues (upper bound):** $19,422.10 → implied rate 0.204% APY
- **Implied Spark spread (upper bound):** -3.857% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = -$367,604.34

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S55 | USDC raw (Avalanche-C — ALM idle) | -$0.00 |
| S54 | Aave Avalanche USDC (aAvaUSDC) | $19,422.10 |

