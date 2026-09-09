# TMF — 2026-08

Treasury Management Function waterfall for the **August 2026** cycle (MSC#12): Sky Net Revenue → Step 1 security & maintenance → Step 2 backstop retention → Step 3 Smart Burn Engine budget → Step 4 staking rewards, plus the Smart Burn Engine's actual execution in August 2026 (kicks, USDS spent, SKY bought) and the on-chain parameter state at month-end. Method: Sky Atlas A.2.3 + TMF Configurations (forum t/28153); engine month = 365/12 days. Pinned inputs are cross-checked against the executive's published parameter block below.

**Spell:** 2026-09-10 Executive — MSC for August 2026 (per TMF Configurations, t/28153) — published in t/28153; not yet executed as of 2026-09-09

## ① Inputs (Step 0)

| Input | Value | Source |
|---|---:|---|
| Sky Net Revenue | 15,745,296 USDS | config/tmf.yaml months['2026-08'].snr (MSC post figure) |
| SKY monthly TWAP | 0.059371239604985234 USDS/SKY | BA Labs observatory TWAP, as quoted in forum t/28153 |
| Aggregate Backstop Capital (Sky Reserves) | 75,728,460.53 USDS | financial.skyeco.com/risk/capital, as quoted in forum t/28153 |
| USDS total supply | 6,366,968,221 USDS | USDS.totalSupply() at block 25878704 |
| kicker.kbump (held fixed) | 6,000 USDS/batch | config/tmf.yaml policy |

## Step 1 — Security & maintenance (20%)

| Line | USDS |
|---|---:|
| Total (20% of SNR) → Core Council Buffer, one transfer | 3,149,059.20 |
| &nbsp;&nbsp;Core Council half (10%) | 1,574,529.60 |
| &nbsp;&nbsp;Fortification Foundation half (10%) | 1,574,529.60 |
| **Remaining after Step 1** | **12,596,236.80** |

## Step 2 — Aggregate Backstop Capital

| Line | Value |
|---|---:|
| Turbo-Fill Floor | 150,000,000 USDS |
| Target Backstop Capital (1.50% x USDS supply) | 95,504,523 USDS |
| Retention rate this month | 50% (below Floor → full retention) |
| Retained (stays in the Surplus Buffer) | 6,298,118.40 USDS |
| **Step 3 remainder (engine budget)** | **6,298,118.40 USDS/month** |

## Step 3 — Smart Burn Engine

| Line | Value |
|---|---:|
| SKY-rewards leg (45%) — buys SKY for stakers | 2,834,153.28 USDS |
| USDS-rewards leg (45%) — paid to stakers as USDS | 2,834,153.28 USDS |
| Burn leg (10%) — buys SKY to burn | 629,811.84 USDS |
| Total buyback (55%) → Flapper | 3,463,965.12 USDS |
| Implied batches / month ÷ / day | 1,049.69 ÷ 34.51 |
| Implied hop (solved, kbump fixed) | 2,503.60 s → **2,504 s** |
| Annual run-rate through the engine | 75,577,421 USDS/yr |
| SBE BEAM bounds | kbump ≤ 12,000: ok · hop ≥ 550 s: ok · ≤ 350,000,000/yr: ok |
| Bought SKY (model estimate, 55% leg ÷ TWAP) | 58,344,160 SKY |

## Step 4 — Staking rewards

| Line | Value |
|---|---:|
| Monthly SKY rewards (45% leg ÷ TWAP) → REWARDS_LSSKY_SKY | 47,736,131.14 SKY |
| Monthly USDS rewards → REWARDS_LSSKY_USDS (via Splitter, per batch) | 2,834,153.28 USDS |
| vestTot (3 months of SKY rewards, 90-day stream) | 143,208,393.43 → **143,208,393 SKY** |
| Stream rate (vestTot ÷ tau) | 18.4167 SKY/s |
| Distributor pull per farm period (7 d) ≈ | 11,138,431 SKY |
| vs MCD_VEST_SKY_TREASURY.cap 70.73 SKY/s | ok |

## ② Parameter block for the spell (computed vs published vs on-chain)

| Parameter | Computed | Published | On-chain @ cast | |
|---|---:|---:|---:|:-:|
| splitter.hop | 2,504 s | 2,504 s | — | ✓ |
| REWARDS_LSSKY_USDS.rewardsDuration | 2,504 s | 2,504 s | — | ✓ |
| splitter.burn | 55% | 55% | — | ✓ |
| kicker.kbump | 6,000 USDS | unchanged | — | |
| vestTot | 143,208,393 SKY | 143,208,393 SKY | — | ✓ |
| vestTau (days) | 90 d | 90 d | — | ✓ |
| vestBgn | block.timestamp at cast | — | — | |
| dist (stream beneficiary) | REWARDS_DIST_LSSKY_SKY | — | — | |
| Core Council Buffer transfer (Step 1) | 3,149,059 USDS | — | — |  |
| SKY to burn (10/55 of window buys) | 2,860,943.76 SKY | 2,860,943.76 SKY | — | ✓ |

## ③ Smart Burn Engine execution in August 2026

Blocks 25,656,293-25,878,704 (2026-08-01 00:00:00 UTC → 2026-08-31 23:59:59 UTC), Splitter `Kick` joined to Flapper `Exec` by transaction.

| Regime (burn / hop) | From | To | Kicks | USDS in | → Flapper | → USDS farm | SKY bought | VWAP |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 100% / 13,787 s | 2026-08-01 02:23:11 UTC | 2026-08-17 13:59:47 UTC | 103 | 618,000 | 618,000 | 0 | 11,365,511.43 | 0.054375 |
| 55% / 3,748 s | 2026-08-17 15:06:35 UTC | 2026-08-31 23:30:23 UTC | 311 | 1,866,000 | 1,026,300 | 839,700 | 15,735,190.69 | 0.065223 |
| **month** | | | **414** | **2,484,000** | **1,644,300** | **839,700** | **27,100,702.11** | **0.060674** |

**Burn attribution** — buys executed from the TMF's first cast (2026-08-17 14:02:23 UTC) are split per the regime they ran under: burn leg = 10% ÷ that regime's `splitter.burn` (10%/55% today), the rest to SKY stakers. Earlier buys (legacy 100% engine) went to the treasury un-attributed (BA Labs convention, t/28153).

| Line | Value |
|---|---:|
| Kicks since the TMF took effect | 311 |
| USDS spent in the window | 1,026,300.00 USDS |
| SKY bought in the window | 15,735,190.69 SKY |
| &nbsp;&nbsp;to SKY stakers (45%/55%) | 12,874,246.93 SKY |
| &nbsp;&nbsp;**to burn (10%/55%)** | **2,860,943.76 SKY** |
| *Alternative reading — 10/55 of every buy in the month (TMF sheet)* | *4,927,400.38 SKY* |

**Parameter changes observed in the month**

| When | Block | Contract | Parameter | Value | Tx |
|---|---:|---|---|---:|---|
| 2026-08-17 14:02:23 UTC | 25,775,271 | MCD_VEST_SKY_TREASURY | vest.yank (id) | 15 | `0x4d4f4d48…` |
| 2026-08-17 14:02:23 UTC | 25,775,271 | MCD_VEST_SKY_TREASURY | vest.init (id) | 16 | `0x4d4f4d48…` |
| 2026-08-17 14:02:23 UTC | 25,775,271 | REWARDS_DIST_LSSKY_SKY | vestId | 16 | `0x4d4f4d48…` |
| 2026-08-17 14:02:23 UTC | 25,775,271 | MCD_SPLIT | hop | 3748 | `0x4d4f4d48…` |
| 2026-08-17 14:02:23 UTC | 25,775,271 | REWARDS_LSSKY_USDS | rewardsDuration | 3748 | `0x4d4f4d48…` |
| 2026-08-17 14:02:23 UTC | 25,775,271 | MCD_SPLIT | burn | 0.55 | `0x4d4f4d48…` |

**SKY farm top-ups** — 7 distributor pulls, 81,975,276.02 SKY moved Pause Proxy → REWARDS_LSSKY_SKY.

| When | Block | SKY | Tx |
|---|---:|---:|---|
| 2026-08-03 12:27:35 UTC | 25,674,364 | 22,170,833.39 | `0x4d325457…` |
| 2026-08-10 11:28:47 UTC | 25,724,298 | 22,169,948.47 | `0x1c2efacd…` |
| 2026-08-17 10:31:11 UTC | 25,774,218 | 22,172,603.23 | `0x6c7c5509…` |
| 2026-08-17 14:02:23 UTC | 25,775,271 | 467,238.77 | `0x4d4f4d48…` |
| 2026-08-17 14:04:59 UTC | 25,775,284 | 1,944.06 | `0x6e614a8c…` |
| 2026-08-24 13:12:23 UTC | 25,825,256 | 7,497,625.17 | `0x4f826358…` |
| 2026-08-31 12:16:23 UTC | 25,875,202 | 7,495,082.94 | `0xfb3f1747…` |

## ④ On-chain state at month-end (block 25,878,704, 2026-08-31 23:59:59 UTC)

| Contract | Parameter | Value |
|---|---|---:|
| MCD_KICK | kbump | 6,000 USDS |
| MCD_KICK | khump (gate offset) | -200,000,000 USDS |
| MCD_SPLIT | hop | 3,748 s |
| MCD_SPLIT | burn | 55% |
| MCD_SPLIT | zzz (last kick) | 2026-08-31 23:30:23 UTC |
| REWARDS_LSSKY_USDS | rewardsDuration | 3,748 s |
| REWARDS_LSSKY_USDS | rewardRate | 0.720384 USDS/s |
| REWARDS_LSSKY_USDS | totalSupply (staked SKY) | 7,954,035,404 |
| REWARDS_LSSKY_SKY | rewardsDuration | 604,800 s |
| REWARDS_LSSKY_SKY | rewardRate | 12.462604 SKY/s |
| REWARDS_LSSKY_SKY | totalSupply (staked SKY) | 9,458,079,006 |
| REWARDS_DIST_LSSKY_SKY | vestId | 16 |
| REWARDS_DIST_LSSKY_SKY | lastDistributedAt | 2026-08-31 12:16:23 UTC |
| MCD_VEST_SKY_TREASURY | stream 16: bgn → fin | 2026-08-17 14:02:23 UTC → 2026-11-15 14:02:23 UTC |
| MCD_VEST_SKY_TREASURY | stream 16: tot / rxd | 96,903,706 / 14,994,652 SKY |
| MCD_VEST_SKY_TREASURY | cap | 70.7305 SKY/s |
| MCD_PAUSE_PROXY | SKY balance | 33,702,776 SKY |
| MCD_VAT / MCD_VOW | vat.dai(vow) - vat.sin(vow) | -64,014,486 USDS |
| MCD_VAT / MCD_VOW | vat.dai(vow) - (sin - Sin - Ash) | 164,181,135 USDS |
| USDS / DAI | totalSupply | 6,366,968,221 / 4,587,708,464 |

**APY snapshot** — this cycle's annualised rewards over the month-end staked balances, TWAP as the SKY price (projection, not a realised yield).

| Farm | Annual rewards | Staked | APY |
|---|---:|---:|---:|
| USDS farm | 34,009,839 USDS | 7,954,035,404 SKY (≈ 472,240,942 USDS) | 7.20% |
| SKY farm | 572,833,574 SKY | 9,458,079,006 SKY | 6.06% |

## Cross-checks

| Check | Computed | Published / reference | |
|---|---:|---:|:-:|
| Step 0 SNR vs settlements/sky_total | 15745296 | 15745296 | ✓ |
| Step 3 capital | 6298118.40 | 6298118.40 | ✓ |
| USDS staking rewards (45%) | 2834153.28 | 2834153.28 | ✓ |
| SKY staking rewards (45% ÷ TWAP) | 47736131.14 | 47736131.14 | ✓ |
| splitter.hop | 2504 | 2504 | ✓ |
| REWARDS_LSSKY_USDS.rewardsDuration | 2504 | 2504 | ✓ |
| splitter.burn | 0.55 | 0.55 | ✓ |
| vestTot | 143208393 | 143208393 | ✓ |
| vestTau (days) | 90 | 90 | ✓ |
| SKY to burn (10/55 of window buys) | 2860943.76 | 2860943.76 | ✓ |
| burn-window kicks | 311 | 311 | ✓ |
| burn-window USDS spent | 1026300 | 1026300 | ✓ |
| burn-window SKY bought | 15735190.69 | 15735190.69 | ✓ |
| burn-window SKY to stakers (45/55) | 12874246.93 | 12874246.93 | ✓ |
| SKY bought in month vs Dune 8544603 | 27100702.111690366847843234 | 27100702.111690357 | ✓ |
| USDS spent in month vs Dune 8544603 | 1644300 | 1644300 | ✓ |
| SKY TWAP: BA observatory vs Dune prices.usd minute-avg | 0.059371239604985234 | 0.05938676030465944 | info |

*Sources: TMF Configurations (forum.skyeco.com/t/28153) · Sky Atlas A.2.3 · dss-flappers (Kicker / Splitter / FlapperUniV2SwapOnly) · on-chain via HyperSync (events) and ETH_RPC (state). Open methodology questions in docs/tmf/README.md.*
