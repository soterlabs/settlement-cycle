# GROVE — Monthly settlement 2026-03

- **Generated:** 2026-04-29T09:46:38+00:00
- **Pipeline:** `settle` v0.1.0
- **Period:** 2026-03-01 → 2026-03-31 (31 days)

## Headline

| Component | Amount (USD) |
|---|---:|
| prime_agent_revenue           | $371,302.48 |
| agent_rate                    | $6,275.43 |
| distribution_rewards          | $0.00 |
| **prime_agent_total_revenue** | **$377,577.90** |
| sky_revenue (net)             | $7,422,472.42 |
| ↳ sky_direct_shortfall (absorbed) | $770,844.78 |

`prime_agent_total_revenue = prime_agent_revenue + agent_rate + distribution_rewards`.
Settlement reports the prime's total revenue (positive cash to the prime) and `sky_revenue` (cost owed to Sky) separately — they're not netted at this layer.

## Pin blocks

| Chain | SoM block | EoM block |
|---|---:|---:|
| avalanche_c | 79250451 | 81789468 |
| base | 42766926 | 44106126 |
| ethereum | 24558867 | 24781026 |
| plume | 52322002 | 58679343 |

## Per-venue breakdown

| Venue | Label | value_som | value_eom | period_inflow | revenue |
|---|---|---:|---:|---:|---:|
| E1 | Aave Horizon RWA RLUSD (aToken) | $140,173,858.07 | $135,317,040.98 | -$5,005,078.23 | $148,261.15 |
| E2 | Aave Horizon RWA USDC (aToken) | $0.00 | $0.00 | $0.00 | $0.00 |
| E3 | Aave Ethereum RLUSD (aToken) | $263,125,087.29 | $138,267,796.42 | -$125,064,023.85 | $206,732.99 |
| E4 | Grove x Steakhouse USDC (Morpho 4626) | $1,002,368.85 | $3,954,260.40 | $2,935,334.38 | $16,557.16 |
| E5 | Grove x Steakhouse USDC High Yield (Morpho 4626) | $1,012,325.68 | $0.00 | -$1,014,076.21 | $1,750.53 |
| E6 | Grove x Steakhouse AUSD (Morpho 4626) | $15,806,005.10 | $18,646,064.04 | $2,800,015.37 | $40,043.57 |
| E7 | Securitize Tokenized AAA CLO Fund (STAC) | $100,804,666.10 | $100,932,589.90 | $0.00 | $127,923.80 |
| E8 | Janus Henderson Anemoy AAA CLO (JAAA) | $455,365,159.12 | $128,240,953.65 | -$327,176,624.42 | $52,418.95 |
| E9 | Janus Henderson Anemoy Treasury Fund (JTRSY) | $561,873,240.34 | $1,160,073,937.73 | $596,049,191.95 | $0.00 |
| E10 | BlackRock USD Institutional Digital Liquidity Fund (BUIDL-I) | $430,205,099.03 | $706,747,881.32 | $274,985,000.00 | $0.00 |
| E11 | Curve AUSD/USDC stableswap LP | $25,000,444.78 | $25,000,938.95 | $0.00 | $494.17 |
| E12 | Uniswap V3 AUSD/USDC pool (NFT positions) | $25,008,038.97 | $25,015,282.51 | $0.00 | $7,243.55 |
| E13 | RLUSD raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| E14 | AUSD raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| E15 | USDC raw (ALM idle) | $0.00 | $9,999,494.27 | $9,999,494.27 | -$0.00 |
| E16 | DAI raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| E17 | USDS raw / POL (ALM idle — already netted out of utilized) | $0.00 | $0.00 | $0.00 | $0.00 |
| E18 | sUSDS raw / POL (ALM idle — Cat B 4626) | $0.00 | $0.00 | $0.00 | $0.00 |
| E19 | Grove x Steakhouse USDC High Yield (Base, Morpho 4626) | $1,004,254.24 | $1,007,405.42 | $0.00 | $3,151.17 |
| E23 | Steakhouse Prime Instant (Base, Morpho V2) | $0.00 | $16,006,741.13 | $15,993,565.71 | $13,175.42 |
| E20 | Janus Henderson Anemoy AAA CLO (JAAA-avalanche) | $256,773,500.00 | $256,878,500.00 | $0.00 | $105,000.00 |
| E21 | Galaxy Arch CLO Token (GACLO-1) | $49,900,000.00 | $49,900,000.00 | $0.00 | $0.00 |
| E22 | Anemoy Tokenized Apollo Diversified Credit Fund (ACRDX) | $51,026,217.85 | $50,674,767.86 | $0.00 | -$351,449.99 |

## Sky Direct (Step 4) breakdown

Per the prime-settlement-methodology, Sky Direct venues book BR_charge to Sky and floor the prime's revenue at zero. When the venue underperforms BR, Sky absorbs the shortfall.

| Venue | actual_revenue | BR_charge | sky_direct_shortfall | prime_keeps |
|---|---:|---:|---:|---:|
| E9 Janus Henderson Anemoy Treasury Fund (JTRSY) | $2,151,505.44 | $2,731,821.74 | $580,316.30 | $0.00 |
| E10 BlackRock USD Institutional Digital Liquidity Fund (BUIDL-I) | $1,557,782.29 | $1,748,310.77 | $190,528.48 | $0.00 |

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
