# SPARK — Monthly settlement 2026-03

- **Generated:** 2026-04-30T12:05:32+00:00
- **Pipeline:** `settle` v0.1.0
- **Period:** 2026-03-01 → 2026-03-31 (31 days)

## Headline

| Component | Amount (USD) |
|---|---:|
| prime_agent_revenue           | $5,710,660.73 |
| agent_rate                    | $0.00 |
| distribution_rewards          | $0.00 |
| **prime_agent_total_revenue** | **$5,710,660.73** |
| sky_revenue (net)             | $9,799,388.74 |

`prime_agent_total_revenue = prime_agent_revenue + agent_rate + distribution_rewards`.
Settlement reports the prime's total revenue (positive cash to the prime) and `sky_revenue` (cost owed to Sky) separately — they're not netted at this layer.

## Pin blocks

| Chain | SoM block | EoM block |
|---|---:|---:|
| arbitrum | 437025050 | 447736930 |
| avalanche_c | 79250451 | 81789468 |
| base | 42766926 | 44106126 |
| ethereum | 24558867 | 24781026 |
| optimism | 148362211 | 149701411 |
| unichain | 41574840 | 44253240 |

## Per-venue breakdown

| Venue | Label | value_som | value_eom | period_inflow | revenue |
|---|---|---:|---:|---:|---:|
| S1 | Spark USDS (SparkLend spToken) | $275,418,608.32 | $150,752,807.53 | -$125,270,347.52 | $604,546.72 |
| S2 | Spark USDC (SparkLend spToken) | $47,958,113.07 | $37,220.76 | -$48,081,075.90 | $160,183.59 |
| S3 | Spark USDT (SparkLend spToken) | $334,974,961.95 | $282,411,706.54 | -$53,138,486.11 | $575,230.70 |
| S4 | Spark DAI (SparkLend spToken) | $246,097,994.85 | $252,839,704.15 | $6,171,379.73 | $570,329.57 |
| S5 | Spark PYUSD (SparkLend spToken) | $100,000,062.96 | $100,000,230.67 | -$71,291.74 | $71,459.45 |
| S6 | Aave Ethereum Lido USDS (aToken) | $0.00 | $0.00 | $0.00 | $0.00 |
| S7 | Aave Ethereum USDS (aToken) | $0.01 | $0.01 | $0.00 | $0.00 |
| S8 | Aave Ethereum USDC (aToken) | $0.06 | $0.06 | $0.00 | $0.00 |
| S9 | Aave Ethereum USDT (aToken) | $0.00 | $495,610,843.87 | $495,610,843.87 | $0.00 |
| S10 | Spark Blue Chip USDC Vault (Morpho) | $977,613.84 | $977,119.89 | -$5,335.95 | $4,842.00 |
| S11 | Spark Blue Chip USDT Vault (Morpho V2) | $0.00 | $40,001.50 | $39,998.02 | $3.48 |
| S12 | Spark DAI Vault (Morpho) | $344.96 | $359.99 | $14.07 | $0.95 |
| S13 | Spark USDS Vault (Morpho) | $21.91 | $21.91 | $0.00 | $0.00 |
| S14 | Maple syrupUSDC (ERC-4626) | $200,002,600.61 | $100,002,159.26 | -$100,416,041.22 | $415,599.88 |
| S15 | Maple syrupUSDT (ERC-4626) | $55,000,541.21 | $100,001,961.58 | $44,829,166.72 | $172,253.65 |
| S16 | Ethena Staked USDe (sUSDe) | $98.88 | $99.17 | $0.00 | $0.29 |
| S17 | Fluid Savings USDS (fsUSDS) | $0.00 | $0.00 | $0.00 | $0.00 |
| S18 | Arkis Spark Prime USDC 1 (ERC-4626) | $10,113,369.18 | $10,156,910.09 | $0.00 | $43,540.91 |
| S19 | BlackRock USD Institutional Digital Liquidity Fund (BUIDL-I) | $0.00 | $0.00 | $0.00 | $30,622.82 |
| S20 | Janus Henderson Anemoy Treasury Fund (JTRSY) | $0.00 | $0.00 | $0.00 | $0.00 |
| S21 | Superstate Short Duration US Government Securities Fund (USTB) | $0.00 | $0.00 | $0.00 | $0.00 |
| S22 | Superstate Crypto Carry Fund (USCC) | $0.00 | $0.00 | $0.00 | $0.00 |
| S23 | Anchorage off-chain custodial position | $0.00 | $0.00 | $0.00 | $0.00 |
| S24 | Spark.fi USDT Reserve Curve (sUSDS/USDT) | $50,000,340.65 | $50,000,879.86 | -$82,936.31 | $83,475.52 |
| S25 | Spark.fi PYUSD Reserve Curve (PYUSD/USDS) | $100,000,617.74 | $100,000,454.45 | -$9,546.01 | $9,382.72 |
| S26 | USDC raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| S27 | USDT raw (ALM idle — $442M as of 2026-04) | $1,868.74 | $0.00 | -$1,868.74 | $0.00 |
| S28 | PYUSD raw (ALM idle — $677M as of 2026-04) | $527,588,174.07 | $555,394,863.22 | $27,806,689.15 | $0.00 |
| S29 | DAI raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| S30 | USDe raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| S31 | USDS raw / POL (ALM idle — already netted out of utilized) | $0.00 | $0.00 | $0.00 | $0.00 |
| S32 | sUSDS raw / POL (ALM — Cat B 4626, $1.59B) | $798,615,399.44 | $1,371,348,954.66 | $569,783,650.09 | $2,949,905.13 |
| S34 | Spark USDC Vault (Morpho, Base) | $494.72 | $427.90 | -$39.76 | -$27.06 |
| S35 | Aave Base USDC (aBasUSDC) | $0.00 | $0.00 | $0.00 | $0.00 |
| S36 | Fluid Savings USDS (fsUSDS, Base) | $283.90 | $283.90 | $0.00 | $0.00 |
| S37 | Savings USDS / sUSDS proxy (Base — POL) | $0.00 | $0.00 | $0.00 | $0.00 |
| S38 | USDS raw (Base — POL) | $65,170,000.00 | $65,170,000.00 | $0.00 | $0.00 |
| S39 | USDC raw (Base — ALM idle) | $5,000,000.00 | $4,997,775.72 | -$2,224.28 | $0.00 |
| S41 | Aave Arbitrum USDCn (aArbUSDCn) | $0.01 | $0.01 | $0.00 | $0.00 |
| S42 | Fluid Savings USDS (fsUSDS, Arbitrum) | $0.00 | $0.00 | $0.00 | $0.00 |
| S43 | Savings USDS / sUSDS proxy (Arbitrum — POL) | $0.00 | $0.00 | $0.00 | $0.00 |
| S44 | USDS raw (Arbitrum — POL) | $90,000,000.00 | $90,000,000.00 | $0.00 | $0.00 |
| S45 | USDC raw (Arbitrum — ALM idle) | $4,983,790.60 | $4,442,753.64 | -$541,036.96 | $0.00 |
| S47 | Savings USDS / sUSDS proxy (Optimism — POL) | $0.00 | $0.00 | $0.00 | $0.00 |
| S48 | USDS raw (Optimism — POL) | $89,900,000.00 | $89,900,000.00 | $0.00 | $0.00 |
| S49 | USDC raw (Optimism — ALM idle) | $5,000,000.00 | $5,000,000.00 | $0.00 | $0.00 |
| S51 | Savings USDS / sUSDS proxy (Unichain — POL) | $0.00 | $0.00 | $0.00 | $0.00 |
| S52 | USDS raw (Unichain — POL) | $89,900,000.00 | $89,900,000.00 | $0.00 | $0.00 |
| S53 | USDC raw (Unichain — ALM idle) | $4,997,665.83 | $4,987,120.65 | -$10,545.18 | $0.00 |
| S54 | Aave Avalanche USDC (aAvaUSDC) | $10,000,143.51 | $10,000,122.64 | -$19,331.28 | $19,310.41 |
| S55 | USDC raw (Avalanche-C — ALM idle) | $0.00 | $10.26 | $10.26 | $0.00 |

## Sky Direct (Step 4) breakdown

Per the prime-settlement-methodology, Sky Direct venues book BR_charge to Sky and floor the prime's revenue at zero. When the venue underperforms BR, Sky absorbs the shortfall.

| Venue | actual_revenue | BR_charge | sky_direct_shortfall | prime_keeps |
|---|---:|---:|---:|---:|
| S19 BlackRock USD Institutional Digital Liquidity Fund (BUIDL-I) | $0.00 | -$30,622.82 | $0.00 | $30,622.82 |

## Formula reference

```
prime_agent_total_revenue = prime_agent_revenue + agent_rate + distribution_rewards
prime_agent_revenue = Σ_venues (value_eom − value_som − period_inflow)
                      with period_inflow = 0 for Cat A idle ALM holdings
                      (off-chain venue gains land as ALM transfers and would
                       be silently netted out otherwise)
agent_rate          = Σ_days subproxy_usds × ((1 + ssr + 0.20%)^(1/365) − 1)
                    + Σ_days subproxy_susds × ((1 + 0.20%)^(1/365) − 1)
distribution_rewards = referral / liquidity-program payouts (placeholder; Phase 3+)
sky_revenue         = Σ_days max(utilized, 0) × ((1 + ssr + 0.30%)^(1/365) − 1)
utilized            = cum_debt − cum_subproxy_usds − cum_subproxy_susds − cum_alm_usds
```

See [`docs/RULES.md`](../../../docs/RULES.md) for SSR history and rate conventions.
