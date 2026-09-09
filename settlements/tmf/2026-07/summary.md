# TMF — 2026-07

Treasury Management Function waterfall for the **July 2026** cycle (MSC#11): Sky Net Revenue → Step 1 security & maintenance → Step 2 backstop retention → Step 3 Smart Burn Engine budget → Step 4 staking rewards, plus the Smart Burn Engine's actual execution in July 2026 (kicks, USDS spent, SKY bought) and the on-chain parameter state at month-end. Method: Sky Atlas A.2.3 + TMF Configurations (forum t/28153); engine month = 365/12 days. Pinned inputs are cross-checked against the executive's published parameter block below.

**Spell:** 2026-08-13 Executive — Initialize SBE BEAM, MSC for July 2026, Increase Buybacks — executed 2026-08-17 14:02:23 UTC (block 25775271)

## ① Inputs (Step 0)

| Input | Value | Source |
|---|---:|---|
| Sky Net Revenue | 10,517,426 USDS | config/tmf.yaml months['2026-07'].snr (MSC post figure) |
| SKY monthly TWAP | 0.058608801023606662 USDS/SKY | BA Labs observatory TWAP, as quoted in forum t/28153 |
| Aggregate Backstop Capital (Sky Reserves) | 80,482,665.96 USDS | financial.skyeco.com/risk/capital, as quoted in forum t/28153 |
| USDS total supply | 6,255,703,158 USDS | USDS.totalSupply() at block 25656292 |
| kicker.kbump (held fixed) | 6,000 USDS/batch | config/tmf.yaml policy |

## Step 1 — Security & maintenance (20%)

| Line | USDS |
|---|---:|
| Total (20% of SNR) → Core Council Buffer, one transfer | 2,103,485.20 |
| &nbsp;&nbsp;Core Council half (10%) | 1,051,742.60 |
| &nbsp;&nbsp;Fortification Foundation half (10%) | 1,051,742.60 |
| **Remaining after Step 1** | **8,413,940.80** |

## Step 2 — Aggregate Backstop Capital

| Line | Value |
|---|---:|
| Turbo-Fill Floor | 150,000,000 USDS |
| Target Backstop Capital (1.50% x USDS supply) | 93,835,547 USDS |
| Retention rate this month | 50% (below Floor → full retention) |
| Retained (stays in the Surplus Buffer) | 4,206,970.40 USDS |
| **Step 3 remainder (engine budget)** | **4,206,970.40 USDS/month** |

## Step 3 — Smart Burn Engine

| Line | Value |
|---|---:|
| SKY-rewards leg (45%) — buys SKY for stakers | 1,893,136.68 USDS |
| USDS-rewards leg (45%) — paid to stakers as USDS | 1,893,136.68 USDS |
| Burn leg (10%) — buys SKY to burn | 420,697.04 USDS |
| Total buyback (55%) → Flapper | 2,313,833.72 USDS |
| Implied batches / month ÷ / day | 701.16 ÷ 23.05 |
| Implied hop (solved, kbump fixed) | 3,748.07 s → **3,748 s** |
| Annual run-rate through the engine | 50,483,645 USDS/yr |
| SBE BEAM bounds | kbump ≤ 12,000: ok · hop ≥ 550 s: ok · ≤ 350,000,000/yr: ok |
| Bought SKY (model estimate, 55% leg ÷ TWAP) | 39,479,288 SKY |

## Step 4 — Staking rewards

| Line | Value |
|---|---:|
| Monthly SKY rewards (45% leg ÷ TWAP) → REWARDS_LSSKY_SKY | 32,301,235.43 SKY |
| Monthly USDS rewards → REWARDS_LSSKY_USDS (via Splitter, per batch) | 1,893,136.68 USDS |
| vestTot (3 months of SKY rewards, 90-day stream) | 96,903,706.28 → **96,903,706 SKY** |
| Stream rate (vestTot ÷ tau) | 12.4619 SKY/s |
| Distributor pull per farm period (7 d) ≈ | 7,536,955 SKY |
| vs MCD_VEST_SKY_TREASURY.cap 70.73 SKY/s | ok |

## ② Parameter block for the spell (computed vs published vs on-chain)

On-chain column read at the spell's execution block 25,775,271 (2026-08-17 14:02:23 UTC).

| Parameter | Computed | Published | On-chain @ cast | |
|---|---:|---:|---:|:-:|
| splitter.hop | 3,748 s | 3,748 s | 3,748 s | ✓ |
| REWARDS_LSSKY_USDS.rewardsDuration | 3,748 s | 3,748 s | 3,748 s | ✓ |
| splitter.burn | 55% | 55% | 55% | ✓ |
| kicker.kbump | 6,000 USDS | unchanged | 6,000 USDS | |
| vestTot | 96,903,706 SKY | 96,903,706 SKY | 96,903,706 SKY | ✓ |
| vestTau (days) | 90 d | 90 d | 90 d | ✓ |
| vestBgn | block.timestamp at cast | 2026-08-17 14:02:23 UTC | 2026-08-17 14:02:23 UTC | |
| dist (stream beneficiary) | REWARDS_DIST_LSSKY_SKY | vestId 16 | vestId 16 | |
| Core Council Buffer transfer (Step 1) | 2,103,485 USDS | 2,103,484 USDS | — | ✓ |
| SKY to burn (10/55 of window buys) | 0.00 SKY | 0.00 SKY | — | ✓ |

## ③ Smart Burn Engine execution in July 2026

Blocks 25,433,939-25,656,292 (2026-07-01 00:00:00 UTC → 2026-07-31 23:59:59 UTC), Splitter `Kick` joined to Flapper `Exec` by transaction.

| Regime (burn / hop) | From | To | Kicks | USDS in | → Flapper | → USDS farm | SKY bought | VWAP |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 100% / 13,787 s | 2026-07-01 01:14:59 UTC | 2026-07-31 22:33:11 UTC | 194 | 1,164,000 | 1,164,000 | 0 | 19,831,814.35 | 0.058694 |
| **month** | | | **194** | **1,164,000** | **1,164,000** | **0** | **19,831,814.35** | **0.058694** |

**Burn attribution** — buys executed from the TMF's first cast (2026-08-17 14:02:23 UTC) are split per the regime they ran under: burn leg = 10% ÷ that regime's `splitter.burn` (10%/55% today), the rest to SKY stakers. Earlier buys (legacy 100% engine) went to the treasury un-attributed (BA Labs convention, t/28153).

| Line | Value |
|---|---:|
| Kicks since the TMF took effect | 0 |
| USDS spent in the window | 0.00 USDS |
| SKY bought in the window | 0.00 SKY |
| &nbsp;&nbsp;to SKY stakers (45%/55%) | 0.00 SKY |
| &nbsp;&nbsp;**to burn (10%/55%)** | **0.00 SKY** |
| *Alternative reading — 10/55 of every buy in the month (TMF sheet)* | *3,605,784.43 SKY* |

**Parameter changes observed in the month**

| When | Block | Contract | Parameter | Value | Tx |
|---|---:|---|---|---:|---|
| 2026-07-20 14:21:59 UTC | 25,574,490 | MCD_VEST_SKY_TREASURY | vest.yank (id) | 14 | `0x6edea958…` |
| 2026-07-20 14:21:59 UTC | 25,574,490 | MCD_VEST_SKY_TREASURY | vest.init (id) | 15 | `0x6edea958…` |
| 2026-07-20 14:21:59 UTC | 25,574,490 | REWARDS_DIST_LSSKY_SKY | vestId | 15 | `0x6edea958…` |

**SKY farm top-ups** — 6 distributor pulls, 78,516,140.93 SKY moved Pause Proxy → REWARDS_LSSKY_SKY.

| When | Block | SKY | Tx |
|---|---:|---:|---|
| 2026-07-06 12:07:47 UTC | 25,473,391 | 18,623,017.16 | `0xca2f740b…` |
| 2026-07-13 11:07:59 UTC | 25,523,307 | 18,622,645.46 | `0xd15f3ef8…` |
| 2026-07-20 10:08:11 UTC | 25,573,227 | 18,622,645.46 | `0xfadb368e…` |
| 2026-07-20 14:21:59 UTC | 25,574,490 | 471,689.93 | `0x6edea958…` |
| 2026-07-20 14:23:35 UTC | 25,574,498 | 3,539.69 | `0x9c07fc6a…` |
| 2026-07-27 13:25:59 UTC | 25,624,432 | 22,172,603.23 | `0x4b051643…` |

## ④ On-chain state at month-end (block 25,656,292, 2026-07-31 23:59:59 UTC)

| Contract | Parameter | Value |
|---|---|---:|
| MCD_KICK | kbump | 6,000 USDS |
| MCD_KICK | khump (gate offset) | -200,000,000 USDS |
| MCD_SPLIT | hop | 13,787 s |
| MCD_SPLIT | burn | 100% |
| MCD_SPLIT | zzz (last kick) | 2026-07-31 22:33:11 UTC |
| REWARDS_LSSKY_USDS | rewardsDuration | 13,787 s |
| REWARDS_LSSKY_USDS | rewardRate | 1.533565 USDS/s |
| REWARDS_LSSKY_USDS | totalSupply (staked SKY) | 66,995,578 |
| REWARDS_LSSKY_SKY | rewardsDuration | 604,800 s |
| REWARDS_LSSKY_SKY | rewardRate | 36.838057 SKY/s |
| REWARDS_LSSKY_SKY | totalSupply (staked SKY) | 17,214,420,533 |
| REWARDS_DIST_LSSKY_SKY | vestId | 15 |
| REWARDS_DIST_LSSKY_SKY | lastDistributedAt | 2026-07-27 13:25:59 UTC |
| MCD_VEST_SKY_TREASURY | stream 15: bgn → fin | 2026-07-20 14:21:59 UTC → 2026-10-18 14:21:59 UTC |
| MCD_VEST_SKY_TREASURY | stream 15: tot / rxd | 286,714,697 / 22,176,143 SKY |
| MCD_VEST_SKY_TREASURY | cap | 70.7305 SKY/s |
| MCD_PAUSE_PROXY | SKY balance | 88,577,350 SKY |
| MCD_VAT / MCD_VOW | vat.dai(vow) - vat.sin(vow) | -74,283,664 USDS |
| MCD_VAT / MCD_VOW | vat.dai(vow) - (sin - Sin - Ash) | 153,911,958 USDS |
| USDS / DAI | totalSupply | 6,255,703,158 / 4,591,260,119 |

**APY snapshot** — this cycle's annualised rewards over the month-end staked balances, TWAP as the SKY price (projection, not a realised yield).

> The USDS farm held under 5% of the SKY farm's stake at month-end — it was dormant / ramping (reactivated 2026-08-17), so its APY here is not meaningful.

| Farm | Annual rewards | Staked | APY |
|---|---:|---:|---:|
| USDS farm | 22,717,640 USDS | 66,995,578 SKY (≈ 3,926,531 USDS) | 578.57% |
| SKY farm | 387,614,825 SKY | 17,214,420,533 SKY | 2.25% |

## Cross-checks

| Check | Computed | Published / reference | |
|---|---:|---:|:-:|
| Step 0 SNR vs settlements/sky_total | 10517426 | 10517426 | ✓ |
| Step 3 capital | 4206970.40 | 4206970.40 | ✓ |
| USDS staking rewards (45%) | 1893136.68 | 1893136.68 | ✓ |
| SKY staking rewards (45% ÷ TWAP) | 32301235.43 | 32301235.43 | ✓ |
| splitter.hop | 3748 | 3748 | ✓ |
| REWARDS_LSSKY_USDS.rewardsDuration | 3748 | 3748 | ✓ |
| splitter.burn | 0.55 | 0.55 | ✓ |
| vestTot | 96903706 | 96903706 | ✓ |
| vestTau (days) | 90 | 90 | ✓ |
| Core Council Buffer transfer (Step 1) | 2103485 | 2103484 | ✓ |
| SKY to burn (10/55 of window buys) | 0.00 | 0 | ✓ |
| SKY bought in month vs Dune 8544603 | 19831814.352667572896053003 | 19831814.352667566 | ✓ |
| USDS spent in month vs Dune 8544603 | 1164000 | 1164000 | ✓ |
| SKY TWAP: BA observatory vs Dune prices.usd minute-avg | 0.058608801023606662 | 0.05859709845430105 | info |
| on-chain splitter.hop after cast | 3748 | 3748 | ✓ |
| on-chain REWARDS_LSSKY_USDS.rewardsDuration after cast | 3748 | 3748 | ✓ |
| on-chain splitter.burn after cast | 0.55 | 0.55 | ✓ |
| on-chain vest.tot(vestId) after cast | 96903706 | 96903706 | ✓ |
| on-chain vest tau after cast (fin - bgn) | 7776000 | 7776000 | ✓ |
| on-chain distributor vestId after cast | 16 | 16 | ✓ |

*Sources: TMF Configurations (forum.skyeco.com/t/28153) · Sky Atlas A.2.3 · dss-flappers (Kicker / Splitter / FlapperUniV2SwapOnly) · on-chain via HyperSync (events) and ETH_RPC (state). Open methodology questions in docs/tmf/README.md.*
