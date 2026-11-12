# TMF — 2026-10

Treasury Management Function waterfall for the **October 2026** cycle (MSC#14): Sky Net Revenue → Step 1 security & maintenance → Step 2 backstop retention → Step 3 Smart Burn Engine budget → Step 4 staking rewards, plus the Smart Burn Engine's actual execution in October 2026 (kicks, USDS spent, SKY bought) and the on-chain parameter state at month-end. Method: Sky Atlas A.2.3 + TMF Configurations (forum t/28153); engine month = 365/12 days. Pinned inputs are cross-checked against the executive's published parameter block below.

**Spell:** October 2026 close — calculated TMF parameters for MSC #14 — calculated proposal; no November execution is assumed

## ① Inputs (Step 0)

| Input | Value | Source |
|---|---:|---|
| Sky Net Revenue | 18,600,412 USDS | settlements/sky_total/2026-10 (rounded to whole USDS) |
| SKY monthly TWAP | 0.075530095541521328 USDS/SKY | BA monthly SKY TWAP, 31 days: https://observatory.data.blockanalitica.com/sky/twap/monthly/?year=2026&month=10 (retrieved 2026-11-12) |
| Aggregate Backstop Capital (Sky Reserves) | 90,093,638.47 USDS | BA historical aggregate_backstop_capital, 2026-10-31: https://sky.data.blockanalitica.com/internal/risk/info/historic/?days_ago=45 (retrieved 2026-11-12) |
| USDS total supply | 6,393,832,506 USDS | USDS.totalSupply() at block 26316188 |
| kicker.kbump (held fixed) | 6,000 USDS/batch | config/tmf.yaml policy |

## Step 1 — Security & maintenance (20%)

| Line | USDS |
|---|---:|
| Total (20% of SNR) → Core Council Buffer, one transfer | 3,720,082.40 |
| &nbsp;&nbsp;Core Council half (10%) | 1,860,041.20 |
| &nbsp;&nbsp;Fortification Foundation half (10%) | 1,860,041.20 |
| **Remaining after Step 1** | **14,880,329.60** |

## Step 2 — Aggregate Backstop Capital

| Line | Value |
|---|---:|
| Turbo-Fill Floor | 150,000,000 USDS |
| Target Backstop Capital (1.50% x USDS supply) | 95,907,488 USDS |
| Retention rate this month | 50% (below Floor → full retention) |
| Retained (stays in the Surplus Buffer) | 7,440,164.80 USDS |
| **Step 3 remainder (engine budget)** | **7,440,164.80 USDS/month** |

## Step 3 — Smart Burn Engine

| Line | Value |
|---|---:|
| SKY-rewards leg (45%) — buys SKY for stakers | 3,348,074.16 USDS |
| USDS-rewards leg (45%) — paid to stakers as USDS | 3,348,074.16 USDS |
| Burn leg (10%) — buys SKY to burn | 744,016.48 USDS |
| Total buyback (55%) → Flapper | 4,092,090.64 USDS |
| Implied batches / month ÷ / day | 1,240.03 ÷ 40.77 |
| Implied hop (solved, kbump fixed) | 2,119.31 s → **2,119 s** |
| Annual run-rate through the engine | 89,281,978 USDS/yr |
| SBE BEAM bounds | kbump ≤ 12,000: ok · hop ≥ 550 s: ok · ≤ 350,000,000/yr: ok |
| Bought SKY (model estimate, 55% leg ÷ TWAP) | 54,178,280 SKY |

## Step 4 — Staking rewards

| Line | Value |
|---|---:|
| Monthly SKY rewards (actual burn-window buys allocated to stakers) → REWARDS_LSSKY_SKY | 34,965,950.76 SKY |
| Monthly USDS rewards → REWARDS_LSSKY_USDS (via Splitter, per batch) | 3,348,074.16 USDS |
| vestTot (3 months of SKY rewards, 90-day stream) | 104,897,852.28 → **104,897,852 SKY** |
| Stream rate (vestTot ÷ tau) | 13.4900 SKY/s |
| Distributor pull per farm period (7 d) ≈ | 8,158,722 SKY |
| vs MCD_VEST_SKY_TREASURY.cap 70.73 SKY/s | ok |

## ② Parameter block for the spell (computed vs published vs on-chain)

| Parameter | Computed | Published | On-chain @ cast | |
|---|---:|---:|---:|:-:|
| splitter.hop | 2,119 s | — | — |  |
| REWARDS_LSSKY_USDS.rewardsDuration | 2,119 s | — | — |  |
| splitter.burn | 55% | — | — |  |
| kicker.kbump | 6,000 USDS | unchanged | — | |
| vestTot | 104,897,852 SKY | — | — |  |
| vestTau (days) | 90 d | — | — |  |
| vestBgn | block.timestamp at cast | — | — | |
| dist (stream beneficiary) | REWARDS_DIST_LSSKY_SKY | — | — | |
| Core Council Buffer transfer (Step 1) | 3,720,082 USDS | — | — |  |
| SKY to burn (10/55 of window buys) | 7,770,211.28 SKY | — | — |  |

## ③ Smart Burn Engine execution in October 2026

Blocks 26,093,738-26,316,188 (2026-10-01 00:00:00 UTC → 2026-10-31 23:59:59 UTC), Splitter `Kick` joined to Flapper `Exec` by transaction.

| Regime (burn / hop) | From | To | Kicks | USDS in | → Flapper | → USDS farm | SKY bought | VWAP |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 55% / 2,504 s | 2026-10-01 00:27:23 UTC | 2026-10-12 13:32:11 UTC | 379 | 2,274,000 | 1,250,700 | 1,023,300 | 14,923,186.72 | 0.083809 |
| 55% / 2,693 s | 2026-10-12 14:19:11 UTC | 2026-10-31 23:36:35 UTC | 595 | 3,570,000 | 1,963,500 | 1,606,500 | 27,812,975.32 | 0.070597 |
| **month** | | | **974** | **5,844,000** | **3,214,200** | **2,629,800** | **42,736,162.04** | **0.075210** |

**Burn attribution** — buys executed from the TMF's first cast (2026-08-17 14:02:23 UTC) are split per the regime they ran under: burn leg = 10% ÷ that regime's `splitter.burn` (10%/55% today), the rest to SKY stakers. Earlier buys (legacy 100% engine) went to the treasury un-attributed (BA Labs convention, t/28153).

| Line | Value |
|---|---:|
| Kicks since the TMF took effect | 974 |
| USDS spent in the window | 3,214,200.00 USDS |
| SKY bought in the window | 42,736,162.04 SKY |
| &nbsp;&nbsp;to SKY stakers (45%/55%) | 34,965,950.76 SKY |
| &nbsp;&nbsp;**to burn (10%/55%)** | **7,770,211.28 SKY** |
| *Alternative reading — 10/55 of every buy in the month (TMF sheet)* | *7,770,211.28 SKY* |

**Parameter changes observed in the month**

| When | Block | Contract | Parameter | Value | Tx |
|---|---:|---|---|---:|---|
| 2026-10-12 14:01:47 UTC | 26,176,859 | MCD_VEST_SKY_TREASURY | vest.yank (id) | 17 | `0x93389082…` |
| 2026-10-12 14:01:47 UTC | 26,176,859 | MCD_VEST_SKY_TREASURY | vest.init (id) | 18 | `0x93389082…` |
| 2026-10-12 14:01:47 UTC | 26,176,859 | REWARDS_DIST_LSSKY_SKY | vestId | 18 | `0x93389082…` |
| 2026-10-12 14:01:47 UTC | 26,176,859 | MCD_SPLIT | hop | 2693 | `0x93389082…` |
| 2026-10-12 14:01:47 UTC | 26,176,859 | REWARDS_LSSKY_USDS | rewardsDuration | 2693 | `0x93389082…` |

**SKY farm top-ups** — 6 distributor pulls, 39,475,446.56 SKY moved Pause Proxy → REWARDS_LSSKY_SKY.

| When | Block | SKY | Tx |
|---|---:|---:|---|
| 2026-10-04 09:49:23 UTC | 26,118,198 | 11,074,340.39 | `0xa2b9df6e…` |
| 2026-10-11 08:51:59 UTC | 26,168,131 | 11,075,003.39 | `0x756de2e4…` |
| 2026-10-12 14:01:47 UTC | 26,176,859 | 1,933,534.31 | `0x93389082…` |
| 2026-10-12 14:03:35 UTC | 26,176,868 | 1,382.30 | `0xeb036046…` |
| 2026-10-19 13:04:23 UTC | 26,226,800 | 7,695,439.49 | `0x06f3bc35…` |
| 2026-10-26 12:05:35 UTC | 26,276,746 | 7,695,746.67 | `0x20579fad…` |

## ④ On-chain state at month-end (block 26,316,188, 2026-10-31 23:59:59 UTC)

| Contract | Parameter | Value |
|---|---|---:|
| MCD_KICK | kbump | 6,000 USDS |
| MCD_KICK | khump (gate offset) | -200,000,000 USDS |
| MCD_SPLIT | hop | 2,693 s |
| MCD_SPLIT | burn | 55% |
| MCD_SPLIT | zzz (last kick) | 2026-10-31 23:36:35 UTC |
| REWARDS_LSSKY_USDS | rewardsDuration | 2,693 s |
| REWARDS_LSSKY_USDS | rewardRate | 1.002599 USDS/s |
| REWARDS_LSSKY_USDS | totalSupply (staked SKY) | 8,717,371,541 |
| REWARDS_LSSKY_SKY | rewardsDuration | 604,800 s |
| REWARDS_LSSKY_SKY | rewardRate | 12.799303 SKY/s |
| REWARDS_LSSKY_SKY | totalSupply (staked SKY) | 8,679,702,908 |
| REWARDS_DIST_LSSKY_SKY | vestId | 18 |
| REWARDS_DIST_LSSKY_SKY | lastDistributedAt | 2026-10-26 12:05:35 UTC |
| MCD_VEST_SKY_TREASURY | stream 18: bgn → fin | 2026-10-12 14:01:47 UTC → 2027-01-10 14:01:47 UTC |
| MCD_VEST_SKY_TREASURY | stream 18: tot / rxd | 99,525,882 / 15,392,568 SKY |
| MCD_VEST_SKY_TREASURY | cap | 70.7305 SKY/s |
| MCD_PAUSE_PROXY | SKY balance | 31,111,342 SKY |
| MCD_VAT / MCD_VOW | vat.dai(vow) - vat.sin(vow) | -50,907,449 USDS |
| MCD_VAT / MCD_VOW | vat.dai(vow) - (sin - Sin - Ash) | 177,288,173 USDS |
| USDS / DAI | totalSupply | 6,393,832,506 / 4,539,413,879 |

**APY snapshot** — this cycle's annualised rewards over the month-end staked balances, TWAP as the SKY price (projection, not a realised yield).

| Farm | Annual rewards | Staked | APY |
|---|---:|---:|---:|
| USDS farm | 40,176,890 USDS | 8,717,371,541 SKY (≈ 658,423,905 USDS) | 6.10% |
| SKY farm | 419,591,409 SKY | 8,679,702,908 SKY | 4.83% |

## Cross-checks

| Check | Computed | Published / reference | |
|---|---:|---:|:-:|
| Step 0 SNR vs settlements/sky_total | 18600412 | 18600412 | ✓ |

*Sources: TMF Configurations (forum.skyeco.com/t/28153) · Sky Atlas A.2.3 · dss-flappers (Kicker / Splitter / FlapperUniV2SwapOnly) · on-chain via HyperSync (events) and ETH_RPC (state). Open methodology questions in docs/tmf/README.md.*
