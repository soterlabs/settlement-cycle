# GROVE — Monthly settlement 2026-02

- **Generated:** 2026-05-04T11:22:06+00:00
- **Pipeline:** `settle` v0.1.0
- **Period:** 2026-02-01 → 2026-02-28 (28 days)

## Headline

| Component | Amount (USD) |
|---|---:|
| prime_agent_revenue           | $859,729.25 |
| agent_rate                    | $5,799.05 |
| distribution_rewards          | $0.00 |
| **prime_agent_total_revenue** | **$865,528.30** |
| sky_revenue (net)             | $5,640,012.59 |

`prime_agent_total_revenue = prime_agent_revenue + agent_rate + distribution_rewards`.
Settlement reports the prime's total revenue (positive cash to the prime) and `sky_revenue` (cost owed to Sky) separately — they're not netted at this layer.

## Pin blocks

| Chain | SoM block | EoM block |
|---|---:|---:|
| avalanche_c | 76986991 | 79250451 |
| base | 41557326 | 42766926 |
| ethereum | 24358292 | 24558867 |
| plume | 49010253 | 52322002 |

## Per-venue breakdown

| Venue | Label | value_som | value_eom | period_inflow | revenue |
|---|---|---:|---:|---:|---:|
| E1 | Aave Horizon RWA RLUSD (aToken) | $101,329,036.60 | $140,173,858.07 | $38,777,225.18 | $67,596.29 |
| E2 | Aave Horizon RWA USDC (aToken) | $11,600,502.75 | $0.00 | -$11,600,502.75 | $0.00 |
| E3 | Aave Ethereum RLUSD (aToken) | $252,762,587.50 | $263,125,087.29 | $10,179,091.86 | $183,407.93 |
| E4 | Grove x Steakhouse USDC (Morpho 4626) | $0.00 | $1,002,368.85 | $1,000,032.95 | $2,335.90 |
| E5 | Grove x Steakhouse USDC High Yield (Morpho 4626) | $1,009,793.26 | $1,012,325.68 | $0.00 | $2,532.42 |
| E6 | Grove x Steakhouse AUSD (Morpho 4626) | $0.00 | $15,806,005.10 | $15,800,308.84 | $5,696.26 |
| E7 | Securitize Tokenized AAA CLO Fund (STAC) | $100,616,387.90 | $100,804,666.10 | $0.00 | $188,278.20 |
| E8 | Janus Henderson Anemoy AAA CLO (JAAA) | $454,188,405.33 | $455,576,922.49 | $0.00 | $394,946.93 |
| E9 | Janus Henderson Anemoy Treasury Fund (JTRSY) | $559,853,396.47 | $561,291,355.38 | $0.00 | $0.00 |
| E10 | BlackRock USD Institutional Digital Liquidity Fund (BUIDL-I) | $179,495,969.98 | $430,205,099.03 | $249,925,000.00 | $0.00 |
| E11 | Curve AUSD/USDC stableswap LP | $0.00 | $25,000,444.78 | $25,000,444.78 | $0.00 |
| E12 | Uniswap V3 AUSD/USDC pool (NFT positions) | $0.00 | $25,008,038.97 | $25,000,000.00 | $8,038.97 |
| E13 | RLUSD raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| E14 | AUSD raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| E15 | USDC raw (ALM idle) | $0.00 | $0.00 | -$0.00 | $0.00 |
| E16 | DAI raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| E17 | USDS raw / POL (ALM idle — already netted out of utilized) | $0.00 | $0.00 | $0.00 | $0.00 |
| E18 | sUSDS raw / POL (ALM idle — Cat B 4626) | $0.00 | $0.00 | $0.00 | $0.00 |
| E19 | Grove x Steakhouse USDC High Yield (Base, Morpho 4626) | $1,001,359.02 | $1,004,254.24 | $0.00 | $2,895.22 |
| E23 | Steakhouse Prime Instant (Base, Morpho V2) | $0.00 | $0.00 | $0.00 | $0.00 |
| E22 | Anemoy Tokenized Apollo Diversified Credit Fund (ACRDX) | $51,022,216.71 | $51,026,217.85 | $0.00 | $4,001.14 |
| E24 | Steakhouse High Yield PYUSD (Morpho 4626) | $0.00 | $0.00 | $0.00 | $0.00 |
| E26 | PYUSD raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| E27 | USDC raw on Base (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |

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
