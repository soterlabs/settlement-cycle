# Spark Savings V2 — per-vault economic view (SPARK 2026-05)

Per `docs/spark/PRD_savings_vaults.md` §5.2. **Display-only.** The pipeline-yield column is an UPPER BOUND — the mapped venues hold capital from both savings-vault depositors and USDS-minted-via-Allocator-Vault, so attributing 100% of their yield to the vault over-attributes by the Allocator-funded share. Read `apr_eff` as a maximum yield envelope and treat implausibly high values (⚠) as co-tenant contamination flags.

## Summary

| Vault | Underlying | TVL avg | VSR liability | VSR APY | Pipeline yield (max) | apr_eff (max) | Net upper bound |
|---|---|---:|---:|---:|---:|---:|---:|
| S56 | USDC | $438,438,722.36 | $2,077,933.25 | 5.580% | $7,250,300.36 | 19.471% ⚠ | $5,172,367.11 |
| S57 | USDT | $1,202,532,072.14 | $2,595,205.75 | 2.541% | $2,084,734.81 | 2.041% | -$510,470.94 |
| S59 | PYUSD | $920,046.83 | $2,640.44 | 3.379% | $3,126,969.85 | 4001.704% ⚠ | $3,124,329.42 |
| S60 | USDC | $31,393,032.43 | $94,676.37 | 3.551% | $0.00 | 0.000% | -$94,676.37 |
| **Σ** | — | — | $4,770,455.81 | — | $12,462,005.03 | — | $7,691,549.22 |

**⚠ implausible apr_eff** — a mapped venue's `actual_revenue` includes yield from non-savings-vault sources (e.g. Anchorage USDC sweeps landing at S26, PayPal PYUSD rewards landing at S28). Reduce the mapping or weight by the vault's share of total ALM underlying.

## S56 — Spark Savings V2 — spUSDC vault

Underlying **USDC**, period 31 days, TVL SoM $573,332,157.53 → EoM $303,545,287.19 (avg $438,438,722.36).

- **VSR liability (Phase A — exact):** $2,077,933.25  → effective rate 5.580% APY
- **Pipeline yield on mapped venues (upper bound):** $7,250,300.36 → implied rate 19.471% APY ⚠ implausibly high — co-tenant attribution likely
- **Implied Spark spread (upper bound):** 13.890% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = $5,172,367.11

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S26 | USDC raw (ALM idle) | $6,162,610.28 |
| S2 | Spark USDC (SparkLend spToken) | $32,502.90 |
| S8 | Aave Ethereum USDC (aToken) | $0.00 |
| S10 | Spark Blue Chip USDC Vault (Morpho) | $609,955.10 |
| S14 | Maple syrupUSDC (ERC-4626) | $423,530.61 |
| S18 | Arkis Spark Prime USDC 1 (ERC-4626) | $21,701.47 |

## S57 — Spark Savings V2 — spUSDT vault

Underlying **USDT**, period 31 days, TVL SoM $1,134,703,231.02 → EoM $1,270,360,913.25 (avg $1,202,532,072.14).

- **VSR liability (Phase A — exact):** $2,595,205.75  → effective rate 2.541% APY
- **Pipeline yield on mapped venues (upper bound):** $2,084,734.81 → implied rate 2.041% APY
- **Implied Spark spread (upper bound):** -0.500% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = -$510,470.94

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S27 | USDT raw (ALM idle — $442M as of 2026-04) | -$194,444.44 |
| S3 | Spark USDT (SparkLend spToken) | $1,868,112.17 |
| S9 | Aave Ethereum USDT (aToken) | $0.02 |
| S11 | Spark Blue Chip USDT Vault (Morpho V2) | $217,269.40 |
| S15 | Maple syrupUSDT (ERC-4626) | $119,641.62 |
| S24 | Spark.fi USDT Reserve Curve (sUSDS/USDT) | $74,156.06 |

## S59 — Spark Savings V2 — spPYUSD vault

Underlying **PYUSD**, period 31 days, TVL SoM $1,066,559.88 → EoM $773,533.78 (avg $920,046.83).

- **VSR liability (Phase A — exact):** $2,640.44  → effective rate 3.379% APY
- **Pipeline yield on mapped venues (upper bound):** $3,126,969.85 → implied rate 4001.704% APY ⚠ implausibly high — co-tenant attribution likely
- **Implied Spark spread (upper bound):** 3998.325% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = $3,124,329.42

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S28 | PYUSD raw (ALM idle — $677M as of 2026-04) | $3,091,580.00 |
| S5 | Spark PYUSD (SparkLend spToken) | $29,003.97 |
| S25 | Spark.fi PYUSD Reserve Curve (PYUSD/USDS) | $6,385.88 |

## S60 — Spark Savings V2 — spUSDC vault (Avalanche-C, CREATE2 same address)

Underlying **USDC**, period 31 days, TVL SoM $37,138,817.43 → EoM $25,647,247.42 (avg $31,393,032.43).

- **VSR liability (Phase A — exact):** $94,676.37  → effective rate 3.551% APY
- **Pipeline yield on mapped venues (upper bound):** $0.00 → implied rate 0.000% APY
- **Implied Spark spread (upper bound):** -3.551% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = -$94,676.37

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S55 | USDC raw (Avalanche-C — ALM idle) | -$0.00 |
| S54 | Aave Avalanche USDC (aAvaUSDC) | $0.00 |

