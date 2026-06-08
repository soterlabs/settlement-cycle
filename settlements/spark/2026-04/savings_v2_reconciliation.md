# Spark Savings V2 — per-vault economic view (SPARK 2026-04)

Per `docs/spark/PRD_savings_vaults.md` §5.2. **Display-only.** The pipeline-yield column is an UPPER BOUND — the mapped venues hold capital from both savings-vault depositors and USDS-minted-via-Allocator-Vault, so attributing 100% of their yield to the vault over-attributes by the Allocator-funded share. Read `apr_eff` as a maximum yield envelope and treat implausibly high values (⚠) as co-tenant contamination flags.

## Summary

| Vault | Underlying | TVL avg | VSR liability | VSR APY | Pipeline yield (max) | apr_eff (max) | Net upper bound |
|---|---|---:|---:|---:|---:|---:|---:|
| S56 | USDC | $515,056,129.94 | $1,302,075.64 | 3.076% | $200,693.42 | 0.474% | -$1,101,382.22 |
| S57 | USDT | $1,001,302,062.18 | $2,183,895.67 | 2.654% | $1,066,918.41 | 1.296% | -$1,116,977.26 |
| S59 | PYUSD | $785,710.72 | $2,803.26 | 4.341% | $3,232,229.49 | 5005.081% ⚠ | $3,229,426.23 |
| S60 | USDC | $55,379,392.84 | $175,138.71 | 3.848% | $0.00 | 0.000% | -$175,138.71 |
| **Σ** | — | — | $3,663,913.27 | — | $4,499,841.32 | — | $835,928.04 |

**⚠ implausible apr_eff** — a mapped venue's `actual_revenue` includes yield from non-savings-vault sources (e.g. Anchorage USDC sweeps landing at S26, PayPal PYUSD rewards landing at S28). Reduce the mapping or weight by the vault's share of total ALM underlying.

## S56 — Spark Savings V2 — spUSDC vault

Underlying **USDC**, period 30 days, TVL SoM $456,780,102.34 → EoM $573,332,157.53 (avg $515,056,129.94).

- **VSR liability (Phase A — exact):** $1,302,075.64  → effective rate 3.076% APY
- **Pipeline yield on mapped venues (upper bound):** $200,693.42 → implied rate 0.474% APY
- **Implied Spark spread (upper bound):** -2.602% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = -$1,101,382.22

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S26 | USDC raw (ALM idle) | $0.00 |
| S2 | Spark USDC (SparkLend spToken) | $125.38 |
| S8 | Aave Ethereum USDC (aToken) | $0.00 |
| S10 | Spark Blue Chip USDC Vault (Morpho) | $9,546.42 |
| S14 | Maple syrupUSDC (ERC-4626) | $172,735.43 |
| S18 | Arkis Spark Prime USDC 1 (ERC-4626) | $18,286.18 |

## S57 — Spark Savings V2 — spUSDT vault

Underlying **USDT**, period 30 days, TVL SoM $867,900,893.34 → EoM $1,134,703,231.02 (avg $1,001,302,062.18).

- **VSR liability (Phase A — exact):** $2,183,895.67  → effective rate 2.654% APY
- **Pipeline yield on mapped venues (upper bound):** $1,066,918.41 → implied rate 1.296% APY
- **Implied Spark spread (upper bound):** -1.357% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = -$1,116,977.26

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S27 | USDT raw (ALM idle — $442M as of 2026-04) | -$0.00 |
| S3 | Spark USDT (SparkLend spToken) | $577,512.14 |
| S9 | Aave Ethereum USDT (aToken) | $0.00 |
| S11 | Spark Blue Chip USDT Vault (Morpho V2) | $168,816.64 |
| S15 | Maple syrupUSDT (ERC-4626) | $204,786.96 |
| S24 | Spark.fi USDT Reserve Curve (sUSDS/USDT) | $115,802.67 |

## S59 — Spark Savings V2 — spPYUSD vault

Underlying **PYUSD**, period 30 days, TVL SoM $504,861.56 → EoM $1,066,559.88 (avg $785,710.72).

- **VSR liability (Phase A — exact):** $2,803.26  → effective rate 4.341% APY
- **Pipeline yield on mapped venues (upper bound):** $3,232,229.49 → implied rate 5005.081% APY ⚠ implausibly high — co-tenant attribution likely
- **Implied Spark spread (upper bound):** 5000.740% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = $3,229,426.23

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S28 | PYUSD raw (ALM idle — $677M as of 2026-04) | $3,176,375.00 |
| S5 | Spark PYUSD (SparkLend spToken) | $46,285.48 |
| S25 | Spark.fi PYUSD Reserve Curve (PYUSD/USDS) | $9,569.01 |

## S60 — Spark Savings V2 — spUSDC vault (Avalanche-C, CREATE2 same address)

Underlying **USDC**, period 30 days, TVL SoM $73,619,968.24 → EoM $37,138,817.43 (avg $55,379,392.84).

- **VSR liability (Phase A — exact):** $175,138.71  → effective rate 3.848% APY
- **Pipeline yield on mapped venues (upper bound):** $0.00 → implied rate 0.000% APY
- **Implied Spark spread (upper bound):** -3.848% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = -$175,138.71

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S55 | USDC raw (Avalanche-C — ALM idle) | $0.00 |
| S54 | Aave Avalanche USDC (aAvaUSDC) | $0.00 |

