# SPARK — Monthly settlement 2026-02

- **Generated:** 2026-05-04T11:22:28+00:00
- **Pipeline:** `settle` v0.1.0
- **Period:** 2026-02-01 → 2026-02-28 (28 days)

## Headline

| Component | Amount (USD) |
|---|---:|
| prime_agent_revenue           | $5,015,853.43 |
| agent_rate                    | $0.00 |
| distribution_rewards          | $0.00 |
| **prime_agent_total_revenue** | **$5,015,853.43** |
| sky_revenue (net)             | $9,887,606.69 |

`prime_agent_total_revenue = prime_agent_revenue + agent_rate + distribution_rewards`.
Settlement reports the prime's total revenue (positive cash to the prime) and `sky_revenue` (cost owed to Sky) separately — they're not netted at this layer.

## Pin blocks

| Chain | SoM block | EoM block |
|---|---:|---:|
| arbitrum | 427315178 | 437025050 |
| avalanche_c | 76986991 | 79250451 |
| base | 41557326 | 42766926 |
| ethereum | 24358292 | 24558867 |
| optimism | 147152611 | 148362211 |
| unichain | 39155640 | 41574840 |

## Per-venue breakdown

| Venue | Label | value_som | value_eom | period_inflow | revenue |
|---|---|---:|---:|---:|---:|
| S1 | Spark USDS (SparkLend spToken) | $556,639,355.91 | $275,418,608.32 | -$282,473,846.17 | $1,253,098.57 |
| S2 | Spark USDC (SparkLend spToken) | $43,659,321.09 | $47,958,113.07 | $4,194,983.35 | $103,808.62 |
| S3 | Spark USDT (SparkLend spToken) | $220,863,532.36 | $334,974,961.95 | $113,698,984.79 | $412,444.80 |
| S4 | Spark DAI (SparkLend spToken) | $305,426,388.32 | $246,097,994.85 | -$60,046,808.95 | $718,415.48 |
| S5 | Spark PYUSD (SparkLend spToken) | $99,995,755.66 | $100,000,062.96 | -$57,005.69 | $61,312.99 |
| S6 | Aave Ethereum Lido USDS (aToken) | $0.00 | $0.00 | $0.00 | $0.00 |
| S7 | Aave Ethereum USDS (aToken) | $0.01 | $0.01 | $0.00 | $0.00 |
| S8 | Aave Ethereum USDC (aToken) | $0.06 | $0.06 | $0.00 | $0.00 |
| S9 | Aave Ethereum USDT (aToken) | $0.00 | $0.00 | $0.00 | $0.00 |
| S10 | Spark Blue Chip USDC Vault (Morpho) | $976,334.44 | $977,613.84 | -$981.47 | $2,260.87 |
| S11 | Spark Blue Chip USDT Vault (Morpho V2) | $0.00 | $0.00 | $0.00 | $0.00 |
| S12 | Spark DAI Vault (Morpho) | $332.92 | $344.96 | $10.78 | $1.26 |
| S13 | Spark USDS Vault (Morpho) | $21.91 | $21.91 | $0.01 | -$0.01 |
| S14 | Maple syrupUSDC (ERC-4626) | $100,000,608.93 | $200,002,600.61 | $99,407,769.34 | $594,222.33 |
| S15 | Maple syrupUSDT (ERC-4626) | $0.00 | $55,000,541.21 | $54,898,506.32 | $102,034.89 |
| S16 | Ethena Staked USDe (sUSDe) | $98.61 | $98.88 | $0.00 | $0.27 |
| S17 | Fluid Savings USDS (fsUSDS) | $0.00 | $0.00 | $0.00 | $0.00 |
| S18 | Arkis Spark Prime USDC 1 (ERC-4626) | $15,106,645.24 | $10,113,369.18 | -$5,051,740.22 | $58,464.16 |
| S19 | BlackRock USD Institutional Digital Liquidity Fund (BUIDL-I) | $0.00 | $0.00 | $0.00 | $0.00 |
| S20 | Janus Henderson Anemoy Treasury Fund (JTRSY) | $0.00 | $0.00 | $0.00 | $0.00 |
| S21 | Superstate Short Duration US Government Securities Fund (USTB) | $0.00 | $0.00 | $0.00 | $0.00 |
| S22 | Superstate Crypto Carry Fund (USCC) | $0.00 | $0.00 | $0.00 | $0.00 |
| S23 | Anchorage off-chain custodial position | $0.00 | $0.00 | $0.00 | $0.00 |
| S24 | Spark.fi USDT Reserve Curve (sUSDS/USDT) | $49,999,911.75 | $50,000,340.65 | -$33,822.17 | $0.00 |
| S25 | Spark.fi PYUSD Reserve Curve (PYUSD/USDS) | $100,000,425.55 | $100,000,617.74 | -$12,634.46 | $12,826.65 |
| S26 | USDC raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| S27 | USDT raw (ALM idle — $442M as of 2026-04) | $0.00 | $1,868.74 | $1,868.74 | $0.00 |
| S28 | PYUSD raw (ALM idle — $677M as of 2026-04) | $524,852,622.17 | $527,588,174.07 | $2,735,551.90 | $0.00 |
| S29 | DAI raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| S30 | USDe raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| S31 | USDS raw / POL (ALM idle — already netted out of utilized) | $0.00 | $0.00 | $0.00 | $0.00 |
| S32 | sUSDS raw / POL (ALM — Cat B 4626, $1.59B) | $393,521,575.97 | $798,615,399.44 | $403,416,716.59 | $1,677,106.88 |
| S34 | Spark USDC Vault (Morpho, Base) | $61.15 | $494.72 | $0.00 | $433.56 |
| S35 | Aave Base USDC (aBasUSDC) | $0.00 | $0.00 | $0.00 | $0.00 |
| S36 | Fluid Savings USDS (fsUSDS, Base) | $283.90 | $283.90 | $0.00 | $0.00 |
| S37 | Savings USDS / sUSDS proxy (Base — POL) | $0.00 | $0.00 | $0.00 | $0.00 |
| S38 | USDS raw (Base — POL) | $65,170,000.00 | $65,170,000.00 | $0.00 | $0.00 |
| S39 | USDC raw (Base — ALM idle) | $4,979,093.44 | $5,000,000.00 | $20,906.56 | $0.00 |
| S41 | Aave Arbitrum USDCn (aArbUSDCn) | $0.01 | $0.01 | $0.00 | $0.00 |
| S42 | Fluid Savings USDS (fsUSDS, Arbitrum) | $0.00 | $0.00 | $0.00 | $0.00 |
| S43 | Savings USDS / sUSDS proxy (Arbitrum — POL) | $0.00 | $0.00 | $0.00 | $0.00 |
| S44 | USDS raw (Arbitrum — POL) | $90,000,000.00 | $90,000,000.00 | $0.00 | $0.00 |
| S45 | USDC raw (Arbitrum — ALM idle) | $4,994,817.32 | $4,983,790.60 | -$11,026.72 | $0.00 |
| S47 | Savings USDS / sUSDS proxy (Optimism — POL) | $0.00 | $0.00 | $0.00 | $0.00 |
| S48 | USDS raw (Optimism — POL) | $89,900,000.00 | $89,900,000.00 | $0.00 | $0.00 |
| S49 | USDC raw (Optimism — ALM idle) | $5,000,000.00 | $5,000,000.00 | $0.00 | $0.00 |
| S51 | Savings USDS / sUSDS proxy (Unichain — POL) | $0.00 | $0.00 | $0.00 | $0.00 |
| S52 | USDS raw (Unichain — POL) | $89,900,000.00 | $89,900,000.00 | $0.00 | $0.00 |
| S53 | USDC raw (Unichain — ALM idle) | $5,000,000.00 | $4,997,665.83 | -$2,334.17 | $0.00 |
| S54 | Aave Avalanche USDC (aAvaUSDC) | $10,000,965.04 | $10,000,143.51 | -$20,243.63 | $19,422.10 |
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
