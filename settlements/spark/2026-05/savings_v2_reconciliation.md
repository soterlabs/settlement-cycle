# Spark Savings V2 — per-vault economic view (SPARK 2026-05)

Per `docs/spark/PRD_savings_vaults.md` §5.2. **Display-only.** Contamination handling: (1) Cat A par-stable venues whose yield comes from `external_alm_sources` sweeps (S26 USDC raw → Anchorage, S28 PYUSD raw → PayPal) are excluded from the mapping in `config/spark.yaml`. (2) The remaining lending venues are weighted by `vault_share = vault_TVL_avg / Σ venue_TVL_avg` to scale out the USDS-minted-via-Allocator co-tenant capital.

**Note — S32 POL agent rate (this period): $322,533.45.** Sky pays Spark the agent rate (+20bps over SSR) on the pooled sUSDS POL at the Spark ETH ALM (funded by both USDS-via-Allocator and savings-vault deposits swapped through PSM3). Routed as a Sky Revenue reduction (parallel to the 30bps `susds_spread_reimbursement`), it reduces what Spark owes Sky by this amount. It is NOT attributed to any single vault in the per-vault table below — surfacing it here for context so the reader can compute Spark's all-in position on savings vaults as `net_spread_weighted_total + pol_agent_rate_total`.

## Summary

| Vault | Underlying | TVL avg | Share | VSR liability | VSR APY | Pipeline yield (raw) | Yield (weighted) | apr_eff (weighted) | Net (weighted) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S56 | USDC | $438,438,722.36 | 100.000% | $2,077,933.25 | 5.580% | $1,087,690.08 | $1,087,690.08 | 2.921% | -$990,243.17 |
| S57 | USDT | $1,202,532,072.14 | 93.767% | $2,595,205.75 | 2.541% | $2,084,734.81 | $1,954,795.60 | 1.914% | -$640,410.15 |
| S59 | PYUSD | $920,046.83 | 0.460% | $2,640.44 | 3.379% | $35,389.85 | $162.80 | 0.208% | -$2,477.64 |
| S60 | USDC | $31,393,032.43 | 100.000% | $94,676.37 | 3.551% | $0.00 | $0.00 | 0.000% | -$94,676.37 |
| **Σ** | — | — | — | $4,770,455.81 | — | — | $3,042,648.49 | — | -$1,727,807.32 |

## S56 — Spark Savings V2 — spUSDC vault

Underlying **USDC**, period 31 days, TVL SoM $573,332,157.53 → EoM $303,545,287.19 (avg $438,438,722.36).

- **VSR liability (Phase A — exact):** $2,077,933.25  → effective rate 5.580% APY
- **Mapped yield venues TVL avg:** $125,623,226.84  → vault_share = 100.000%
- **Pipeline yield (raw, all co-tenants):** $1,087,690.08  → implied rate 2.921% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $1,087,690.08 → implied rate 2.921% APY
- **Implied Spark spread (weighted):** -2.659% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$990,243.17

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S2 | Spark USDC (SparkLend spToken) | $32,502.90 |
| S8 | Aave Ethereum USDC (aToken) | $0.00 |
| S10 | Spark Blue Chip USDC Vault (Morpho) | $609,955.10 |
| S14 | Maple syrupUSDC (ERC-4626) | $423,530.61 |
| S18 | Arkis Spark Prime USDC 1 (ERC-4626) | $21,701.47 |

## S57 — Spark Savings V2 — spUSDT vault

Underlying **USDT**, period 31 days, TVL SoM $1,134,703,231.02 → EoM $1,270,360,913.25 (avg $1,202,532,072.14).

- **VSR liability (Phase A — exact):** $2,595,205.75  → effective rate 2.541% APY
- **Mapped yield venues TVL avg:** $1,282,466,806.90  → vault_share = 93.767%
- **Pipeline yield (raw, all co-tenants):** $2,084,734.81  → implied rate 2.041% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $1,954,795.60 → implied rate 1.914% APY
- **Implied Spark spread (weighted):** -0.627% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$640,410.15

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S3 | Spark USDT (SparkLend spToken) | $1,868,112.17 |
| S9 | Aave Ethereum USDT (aToken) | $0.02 |
| S11 | Spark Blue Chip USDT Vault (Morpho V2) | $217,269.40 |
| S15 | Maple syrupUSDT (ERC-4626) | $119,641.62 |
| S24 | Spark.fi USDT Reserve Curve (sUSDS/USDT) | $74,156.06 |
| S27 | USDT raw (ALM idle — $442M as of 2026-04) | -$194,444.44 |

## S59 — Spark Savings V2 — spPYUSD vault

Underlying **PYUSD**, period 31 days, TVL SoM $1,066,559.88 → EoM $773,533.78 (avg $920,046.83).

- **VSR liability (Phase A — exact):** $2,640.44  → effective rate 3.379% APY
- **Mapped yield venues TVL avg:** $199,999,831.08  → vault_share = 0.460%
- **Pipeline yield (raw, all co-tenants):** $35,389.85  → implied rate 45.290% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $162.80 → implied rate 0.208% APY
- **Implied Spark spread (weighted):** -3.171% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$2,477.64

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S5 | Spark PYUSD (SparkLend spToken) | $29,003.97 |
| S25 | Spark.fi PYUSD Reserve Curve (PYUSD/USDS) | $6,385.88 |

## S60 — Spark Savings V2 — spUSDC vault (Avalanche-C, CREATE2 same address)

Underlying **USDC**, period 31 days, TVL SoM $37,138,817.43 → EoM $25,647,247.42 (avg $31,393,032.43).

- **VSR liability (Phase A — exact):** $94,676.37  → effective rate 3.551% APY
- **Mapped yield venues TVL avg:** $1,250.27  → vault_share = 100.000%
- **Pipeline yield (raw, all co-tenants):** $0.00  → implied rate 0.000% APY (upper bound, ignore)
- **Pipeline yield (weighted to vault share):** $0.00 → implied rate 0.000% APY
- **Implied Spark spread (weighted):** -3.551% APY  = apr_eff_weighted − vsr_apr_eff
- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = -$94,676.37

Per-venue yield contributions (raw, pre-weighting):

| Venue | Label | actual_revenue |
|---|---|---:|
| S55 | USDC raw (Avalanche-C — ALM idle) | -$0.00 |
| S54 | Aave Avalanche USDC (aAvaUSDC) | $0.00 |

