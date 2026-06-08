# Spark Savings V2 — per-vault economic view (SPARK 2026-03)

Per `docs/spark/PRD_savings_vaults.md` §5.2. **Display-only.** The pipeline-yield column is an UPPER BOUND — the mapped venues hold capital from both savings-vault depositors and USDS-minted-via-Allocator-Vault, so attributing 100% of their yield to the vault over-attributes by the Allocator-funded share. Read `apr_eff` as a maximum yield envelope and treat implausibly high values (⚠) as co-tenant contamination flags.

## Summary

| Vault | Underlying | TVL avg | VSR liability | VSR APY | Pipeline yield (max) | apr_eff (max) | Net upper bound |
|---|---|---:|---:|---:|---:|---:|---:|
| S56 | USDC | $372,280,466.38 | $1,212,564.10 | 3.835% | $1,268,964.42 | 4.013% | $56,400.31 |
| S57 | USDT | $627,939,145.38 | $1,295,372.43 | 2.429% | $831,112.77 | 1.558% | -$464,259.67 |
| S59 | PYUSD | $299,652.64 | $1,245.29 | 4.893% | $2,740,661.17 | 10768.827% ⚠ | $2,739,415.87 |
| S60 | USDC | $113,238,553.72 | $421,343.62 | 4.381% | $19,310.41 | 0.201% | -$402,033.21 |
| **Σ** | — | — | $2,930,525.46 | — | $4,860,048.76 | — | $1,929,523.30 |

**⚠ implausible apr_eff** — a mapped venue's `actual_revenue` includes yield from non-savings-vault sources (e.g. Anchorage USDC sweeps landing at S26, PayPal PYUSD rewards landing at S28). Reduce the mapping or weight by the vault's share of total ALM underlying.

## S56 — Spark Savings V2 — spUSDC vault

Underlying **USDC**, period 31 days, TVL SoM $287,780,830.42 → EoM $456,780,102.34 (avg $372,280,466.38).

- **VSR liability (Phase A — exact):** $1,212,564.10  → effective rate 3.835% APY
- **Pipeline yield on mapped venues (upper bound):** $1,268,964.42 → implied rate 4.013% APY
- **Implied Spark spread (upper bound):** 0.178% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = $56,400.31

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S26 | USDC raw (ALM idle) | $805,479.00 |
| S2 | Spark USDC (SparkLend spToken) | $0.00 |
| S8 | Aave Ethereum USDC (aToken) | $0.00 |
| S10 | Spark Blue Chip USDC Vault (Morpho) | $4,849.50 |
| S14 | Maple syrupUSDC (ERC-4626) | $415,095.01 |
| S18 | Arkis Spark Prime USDC 1 (ERC-4626) | $43,540.91 |

## S57 — Spark Savings V2 — spUSDT vault

Underlying **USDT**, period 31 days, TVL SoM $387,977,397.42 → EoM $867,900,893.34 (avg $627,939,145.38).

- **VSR liability (Phase A — exact):** $1,295,372.43  → effective rate 2.429% APY
- **Pipeline yield on mapped venues (upper bound):** $831,112.77 → implied rate 1.558% APY
- **Implied Spark spread (upper bound):** -0.871% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = -$464,259.67

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S27 | USDT raw (ALM idle — $442M as of 2026-04) | -$0.00 |
| S3 | Spark USDT (SparkLend spToken) | $575,230.70 |
| S9 | Aave Ethereum USDT (aToken) | $0.00 |
| S11 | Spark Blue Chip USDT Vault (Morpho V2) | $3.48 |
| S15 | Maple syrupUSDT (ERC-4626) | $172,403.07 |
| S24 | Spark.fi USDT Reserve Curve (sUSDS/USDT) | $83,475.52 |

## S59 — Spark Savings V2 — spPYUSD vault

Underlying **PYUSD**, period 31 days, TVL SoM $94,443.72 → EoM $504,861.56 (avg $299,652.64).

- **VSR liability (Phase A — exact):** $1,245.29  → effective rate 4.893% APY
- **Pipeline yield on mapped venues (upper bound):** $2,740,661.17 → implied rate 10768.827% APY ⚠ implausibly high — co-tenant attribution likely
- **Implied Spark spread (upper bound):** 10763.934% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = $2,739,415.87

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S28 | PYUSD raw (ALM idle — $677M as of 2026-04) | $2,659,819.00 |
| S5 | Spark PYUSD (SparkLend spToken) | $71,459.45 |
| S25 | Spark.fi PYUSD Reserve Curve (PYUSD/USDS) | $9,382.72 |

## S60 — Spark Savings V2 — spUSDC vault (Avalanche-C, CREATE2 same address)

Underlying **USDC**, period 31 days, TVL SoM $152,857,139.20 → EoM $73,619,968.24 (avg $113,238,553.72).

- **VSR liability (Phase A — exact):** $421,343.62  → effective rate 4.381% APY
- **Pipeline yield on mapped venues (upper bound):** $19,310.41 → implied rate 0.201% APY
- **Implied Spark spread (upper bound):** -4.180% APY  = apr_eff − vsr_apr_eff
- **Net upper bound to Spark:** pipeline_yield − vsr_liability = -$402,033.21

Per-venue yield contributions (sum = pipeline yield above):

| Venue | Label | actual_revenue |
|---|---|---:|
| S55 | USDC raw (Avalanche-C — ALM idle) | -$0.00 |
| S54 | Aave Avalanche USDC (aAvaUSDC) | $19,310.41 |

