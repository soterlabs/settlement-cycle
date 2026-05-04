# SPARK — Monthly settlement 2026-01

- **Generated:** 2026-05-04T11:22:22+00:00
- **Pipeline:** `settle` v0.1.0
- **Period:** 2026-01-01 → 2026-01-31 (31 days)

## Headline

| Component | Amount (USD) |
|---|---:|
| prime_agent_revenue           | $5,661,691.70 |
| agent_rate                    | $0.00 |
| distribution_rewards          | $0.00 |
| **prime_agent_total_revenue** | **$5,661,691.70** |
| sky_revenue (net)             | $10,477,411.26 |

`prime_agent_total_revenue = prime_agent_revenue + agent_rate + distribution_rewards`.
Settlement reports the prime's total revenue (positive cash to the prime) and `sky_revenue` (cost owed to Sky) separately — they're not netted at this layer.

## Pin blocks

| Chain | SoM block | EoM block |
|---|---:|---:|
| arbitrum | 416593973 | 427315178 |
| avalanche_c | 74824633 | 76986991 |
| base | 40218126 | 41557326 |
| ethereum | 24136052 | 24358292 |
| optimism | 145813411 | 147152611 |
| unichain | 36477240 | 39155640 |

## Per-venue breakdown

| Venue | Label | value_som | value_eom | period_inflow | revenue |
|---|---|---:|---:|---:|---:|
| S1 | Spark USDS (SparkLend spToken) | $177,429,260.51 | $556,639,355.91 | $378,751,816.89 | $458,278.51 |
| S2 | Spark USDC (SparkLend spToken) | $43,177,397.52 | $43,659,321.09 | $336,431.14 | $145,492.43 |
| S3 | Spark USDT (SparkLend spToken) | $339,412,952.95 | $220,863,532.36 | -$119,515,986.19 | $966,565.60 |
| S4 | Spark DAI (SparkLend spToken) | $332,838,656.14 | $305,426,388.32 | -$28,309,659.90 | $897,392.08 |
| S5 | Spark PYUSD (SparkLend spToken) | $144,386,807.12 | $99,995,755.66 | -$44,629,741.29 | $238,689.83 |
| S6 | Aave Ethereum Lido USDS (aToken) | $0.00 | $0.00 | $0.00 | $0.00 |
| S7 | Aave Ethereum USDS (aToken) | $0.01 | $0.01 | $0.00 | $0.00 |
| S8 | Aave Ethereum USDC (aToken) | $0.06 | $0.06 | $0.00 | $0.00 |
| S9 | Aave Ethereum USDT (aToken) | $0.00 | $0.00 | $0.00 | $0.00 |
| S10 | Spark Blue Chip USDC Vault (Morpho) | $10,124,757.36 | $976,334.44 | -$9,171,336.56 | $22,913.63 |
| S11 | Spark Blue Chip USDT Vault (Morpho V2) | $0.00 | $0.00 | $0.00 | $0.00 |
| S12 | Spark DAI Vault (Morpho) | $286.49 | $332.92 | $44.12 | $2.32 |
| S13 | Spark USDS Vault (Morpho) | $21.89 | $21.91 | $0.00 | $0.01 |
| S14 | Maple syrupUSDC (ERC-4626) | $300,001,048.43 | $100,000,608.93 | -$200,851,794.16 | $851,354.67 |
| S15 | Maple syrupUSDT (ERC-4626) | $0.00 | $0.00 | $0.00 | $0.00 |
| S16 | Ethena Staked USDe (sUSDe) | $98.22 | $98.61 | $0.00 | $0.39 |
| S17 | Fluid Savings USDS (fsUSDS) | $0.00 | $0.00 | $0.00 | $0.00 |
| S18 | Arkis Spark Prime USDC 1 (ERC-4626) | $15,024,030.59 | $15,106,645.24 | $0.00 | $82,614.64 |
| S19 | BlackRock USD Institutional Digital Liquidity Fund (BUIDL-I) | $0.00 | $0.00 | $0.00 | $0.00 |
| S20 | Janus Henderson Anemoy Treasury Fund (JTRSY) | $0.00 | $0.00 | $0.00 | $0.00 |
| S21 | Superstate Short Duration US Government Securities Fund (USTB) | $0.00 | $0.00 | $0.00 | $0.00 |
| S22 | Superstate Crypto Carry Fund (USCC) | $0.00 | $0.00 | $0.00 | $0.00 |
| S23 | Anchorage off-chain custodial position | $0.00 | $0.00 | $0.00 | $0.00 |
| S24 | Spark.fi USDT Reserve Curve (sUSDS/USDT) | $50,000,864.48 | $49,999,911.75 | -$28,449.77 | $0.00 |
| S25 | Spark.fi PYUSD Reserve Curve (PYUSD/USDS) | $100,000,415.95 | $100,000,425.55 | -$7,422.05 | $7,431.66 |
| S26 | USDC raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| S27 | USDT raw (ALM idle — $442M as of 2026-04) | $0.00 | $0.00 | $0.00 | $0.00 |
| S28 | PYUSD raw (ALM idle — $677M as of 2026-04) | $478,728,977.46 | $524,852,622.17 | $46,123,644.72 | $0.00 |
| S29 | DAI raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| S30 | USDe raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| S31 | USDS raw / POL (ALM idle — already netted out of utilized) | $0.00 | $0.00 | $0.00 | $0.00 |
| S32 | sUSDS raw / POL (ALM — Cat B 4626, $1.59B) | $529,568,618.85 | $393,521,575.97 | -$137,788,683.49 | $1,741,640.61 |
| S34 | Spark USDC Vault (Morpho, Base) | $167,771,852.13 | $61.15 | -$167,989,509.35 | $217,718.37 |
| S35 | Aave Base USDC (aBasUSDC) | $0.00 | $0.00 | $0.00 | $0.00 |
| S36 | Fluid Savings USDS (fsUSDS, Base) | $283.90 | $283.90 | $0.00 | $0.00 |
| S37 | Savings USDS / sUSDS proxy (Base — POL) | $0.00 | $0.00 | $0.00 | $0.00 |
| S38 | USDS raw (Base — POL) | $65,170,000.00 | $65,170,000.00 | $0.00 | $0.00 |
| S39 | USDC raw (Base — ALM idle) | $4,732,058.26 | $4,979,093.44 | $247,035.17 | $0.00 |
| S41 | Aave Arbitrum USDCn (aArbUSDCn) | $0.01 | $0.01 | $0.00 | $0.00 |
| S42 | Fluid Savings USDS (fsUSDS, Arbitrum) | $0.00 | $0.00 | $0.00 | $0.00 |
| S43 | Savings USDS / sUSDS proxy (Arbitrum — POL) | $0.00 | $0.00 | $0.00 | $0.00 |
| S44 | USDS raw (Arbitrum — POL) | $90,000,000.00 | $90,000,000.00 | $0.00 | $0.00 |
| S45 | USDC raw (Arbitrum — ALM idle) | $5,000,000.00 | $4,994,817.32 | -$5,182.68 | $0.00 |
| S47 | Savings USDS / sUSDS proxy (Optimism — POL) | $0.00 | $0.00 | $0.00 | $0.00 |
| S48 | USDS raw (Optimism — POL) | $89,900,000.00 | $89,900,000.00 | $0.00 | $0.00 |
| S49 | USDC raw (Optimism — ALM idle) | $5,000,000.00 | $5,000,000.00 | $0.00 | $0.00 |
| S51 | Savings USDS / sUSDS proxy (Unichain — POL) | $0.00 | $0.00 | $0.00 | $0.00 |
| S52 | USDS raw (Unichain — POL) | $89,900,000.00 | $89,900,000.00 | $0.00 | $0.00 |
| S53 | USDC raw (Unichain — ALM idle) | $5,000,000.00 | $5,000,000.00 | $0.00 | $0.00 |
| S54 | Aave Avalanche USDC (aAvaUSDC) | $10,000,438.06 | $10,000,965.04 | -$31,069.97 | $31,596.95 |
| S55 | USDC raw (Avalanche-C — ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |

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
