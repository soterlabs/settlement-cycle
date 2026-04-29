# GROVE — Monthly settlement 2026-01

- **Generated:** 2026-04-29T09:45:32+00:00
- **Pipeline:** `settle` v0.1.0
- **Period:** 2026-01-01 → 2026-01-31 (31 days)

## Headline

| Component | Amount (USD) |
|---|---:|
| prime_agent_revenue           | $5,870,545.78 |
| agent_rate                    | $6,263.84 |
| distribution_rewards          | $0.00 |
| **prime_agent_total_revenue** | **$5,876,809.61** |
| sky_revenue (net)             | $6,526,237.69 |
| ↳ sky_direct_shortfall (absorbed) | $532,390.02 |

`prime_agent_total_revenue = prime_agent_revenue + agent_rate + distribution_rewards`.
Settlement reports the prime's total revenue (positive cash to the prime) and `sky_revenue` (cost owed to Sky) separately — they're not netted at this layer.

## Pin blocks

| Chain | SoM block | EoM block |
|---|---:|---:|
| avalanche_c | 74824633 | 76986991 |
| base | 40218126 | 41557326 |
| ethereum | 24136052 | 24358292 |
| plume | 44691271 | 49010253 |

## Per-venue breakdown

| Venue | Label | value_som | value_eom | period_inflow | revenue |
|---|---|---:|---:|---:|---:|
| E1 | Aave Horizon RWA RLUSD (aToken) | $50,248,550.46 | $101,329,036.60 | $51,004,936.85 | $75,549.29 |
| E2 | Aave Horizon RWA USDC (aToken) | $10,056,422.20 | $11,600,502.75 | $1,504,589.71 | $39,490.84 |
| E3 | Aave Ethereum RLUSD (aToken) | $241,966,197.04 | $252,762,587.50 | $10,567,061.59 | $229,328.86 |
| E4 | Grove x Steakhouse USDC (Morpho 4626) | $0.00 | $0.00 | $0.00 | $0.00 |
| E5 | Grove x Steakhouse USDC High Yield (Morpho 4626) | $0.00 | $1,009,793.26 | $999,752.65 | $10,040.61 |
| E6 | Grove x Steakhouse AUSD (Morpho 4626) | $0.00 | $0.00 | $0.00 | $0.00 |
| E7 | Securitize Tokenized AAA CLO Fund (STAC) | $100,000,000.00 | $100,616,387.90 | $0.00 | $616,387.90 |
| E8 | Janus Henderson Anemoy AAA CLO (JAAA) | $751,935,242.23 | $454,512,148.04 | -$300,086,264.88 | $2,663,170.69 |
| E9 | Janus Henderson Anemoy Treasury Fund (JTRSY) | $259,049,453.22 | $560,288,806.18 | $300,225,591.59 | $0.00 |
| E10 | BlackRock USD Institutional Digital Liquidity Fund (BUIDL-I) | $178,974,854.01 | $179,495,969.98 | $0.00 | $0.00 |
| E11 | Curve AUSD/USDC stableswap LP | $0.00 | $0.00 | $0.00 | $0.00 |
| E12 | Uniswap V3 AUSD/USDC pool (NFT positions) | $0.00 | $0.00 | $0.00 | $0.00 |
| E13 | RLUSD raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| E14 | AUSD raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| E15 | USDC raw (ALM idle) | $0.00 | $0.00 | -$0.00 | $0.00 |
| E16 | DAI raw (ALM idle) | $0.00 | $0.00 | $0.00 | $0.00 |
| E17 | USDS raw / POL (ALM idle — already netted out of utilized) | $0.00 | $0.00 | $0.00 | $0.00 |
| E18 | sUSDS raw / POL (ALM idle — Cat B 4626) | $0.00 | $0.00 | $0.00 | $0.00 |
| E19 | Grove x Steakhouse USDC High Yield (Base, Morpho 4626) | $0.00 | $1,001,359.02 | $1,000,018.22 | $1,340.80 |
| E23 | Steakhouse Prime Instant (Base, Morpho V2) | $0.00 | $0.00 | $0.00 | $0.00 |
| E20 | Janus Henderson Anemoy AAA CLO (JAAA-avalanche) | $255,065,250.00 | $256,292,500.00 | $0.00 | $1,227,250.00 |
| E21 | Galaxy Arch CLO Token (GACLO-1) | $49,900,000.00 | $49,900,000.00 | $0.00 | $0.00 |
| E22 | Anemoy Tokenized Apollo Diversified Credit Fund (ACRDX) | $50,014,229.92 | $51,022,216.71 | $0.00 | $1,007,986.79 |

## Sky Direct (Step 4) breakdown

Per the prime-settlement-methodology, Sky Direct venues book BR_charge to Sky and floor the prime's revenue at zero. When the venue underperforms BR, Sky absorbs the shortfall.

| Venue | actual_revenue | BR_charge | sky_direct_shortfall | prime_keeps |
|---|---:|---:|---:|---:|
| E9 Janus Henderson Anemoy Treasury Fund (JTRSY) | $1,013,761.38 | $1,447,319.81 | $433,558.43 | $0.00 |
| E10 BlackRock USD Institutional Digital Liquidity Fund (BUIDL-I) | $521,115.97 | $619,947.56 | $98,831.59 | $0.00 |

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
