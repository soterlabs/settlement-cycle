# TMF — 2026-09

Treasury Management Function waterfall for the **September 2026** cycle (MSC#13): Sky Net Revenue → Step 1 security & maintenance → Step 2 backstop retention → Step 3 Smart Burn Engine budget → Step 4 staking rewards, plus the Smart Burn Engine's actual execution in September 2026 (kicks, USDS spent, SKY bought) and the on-chain parameter state at month-end. Method: Sky Atlas A.2.3 + TMF Configurations (forum t/28153); engine month = 365/12 days. Pinned inputs are cross-checked against the executive's published parameter block below.

**Spell:** September 2026 close — calculated TMF parameters for the next MSC — calculated proposal; no executed October parameter update is assumed

## ① Inputs (Step 0)

| Input | Value | Source |
|---|---:|---|
| Sky Net Revenue | 14,635,947 USDS | settlements/sky_total/2026-09 (rounded to whole USDS) |
| SKY monthly TWAP | 0.068846535412547569 USDS/SKY | BA monthly SKY TWAP, 30 days: https://observatory.data.blockanalitica.com/sky/twap/monthly/?year=2026&month=9 (retrieved 2026-10-01) |
| Aggregate Backstop Capital (Sky Reserves) | 84,550,765.96 USDS | BA historical aggregate_backstop_capital, 2026-09-30: https://sky.data.blockanalitica.com/internal/risk/info/historic/?days_ago=30 (retrieved 2026-10-01) |
| USDS total supply | 6,686,161,482 USDS | USDS.totalSupply() at block 26093737 |
| kicker.kbump (held fixed) | 6,000 USDS/batch | config/tmf.yaml policy |

## Step 1 — Security & maintenance (20%)

| Line | USDS |
|---|---:|
| Total (20% of SNR) → Core Council Buffer, one transfer | 2,927,189.40 |
| &nbsp;&nbsp;Core Council half (10%) | 1,463,594.70 |
| &nbsp;&nbsp;Fortification Foundation half (10%) | 1,463,594.70 |
| **Remaining after Step 1** | **11,708,757.60** |

## Step 2 — Aggregate Backstop Capital

| Line | Value |
|---|---:|
| Turbo-Fill Floor | 150,000,000 USDS |
| Target Backstop Capital (1.50% x USDS supply) | 100,292,422 USDS |
| Retention rate this month | 50% (below Floor → full retention) |
| Retained (stays in the Surplus Buffer) | 5,854,378.80 USDS |
| **Step 3 remainder (engine budget)** | **5,854,378.80 USDS/month** |

## Step 3 — Smart Burn Engine

| Line | Value |
|---|---:|
| SKY-rewards leg (45%) — buys SKY for stakers | 2,634,470.46 USDS |
| USDS-rewards leg (45%) — paid to stakers as USDS | 2,634,470.46 USDS |
| Burn leg (10%) — buys SKY to burn | 585,437.88 USDS |
| Total buyback (55%) → Flapper | 3,219,908.34 USDS |
| Implied batches / month ÷ / day | 975.73 ÷ 32.08 |
| Implied hop (solved, kbump fixed) | 2,693.37 s → **2,693 s** |
| Annual run-rate through the engine | 70,252,546 USDS/yr |
| SBE BEAM bounds | kbump ≤ 12,000: ok · hop ≥ 550 s: ok · ≤ 350,000,000/yr: ok |
| Bought SKY (model estimate, 55% leg ÷ TWAP) | 46,769,359 SKY |

## Step 4 — Staking rewards

| Line | Value |
|---|---:|
| Monthly SKY rewards (45% leg ÷ TWAP) → REWARDS_LSSKY_SKY | 38,265,839.29 SKY |
| Monthly USDS rewards → REWARDS_LSSKY_USDS (via Splitter, per batch) | 2,634,470.46 USDS |
| vestTot (3 months of SKY rewards, 90-day stream) | 114,797,517.88 → **114,797,518 SKY** |
| Stream rate (vestTot ÷ tau) | 14.7631 SKY/s |
| Distributor pull per farm period (7 d) ≈ | 8,928,696 SKY |
| vs MCD_VEST_SKY_TREASURY.cap 70.73 SKY/s | ok |

## ② Parameter block for the spell (computed vs published vs on-chain)

| Parameter | Computed | Published | On-chain @ cast | |
|---|---:|---:|---:|:-:|
| splitter.hop | 2,693 s | — | — |  |
| REWARDS_LSSKY_USDS.rewardsDuration | 2,693 s | — | — |  |
| splitter.burn | 55% | — | — |  |
| kicker.kbump | 6,000 USDS | unchanged | — | |
| vestTot | 114,797,518 SKY | — | — |  |
| vestTau (days) | 90 d | — | — |  |
| vestBgn | block.timestamp at cast | — | — | |
| dist (stream beneficiary) | REWARDS_DIST_LSSKY_SKY | — | — | |
| Core Council Buffer transfer (Step 1) | 2,927,189 USDS | — | — |  |
| SKY to burn (10/55 of window buys) | 7,372,287.58 SKY | — | — |  |

## ③ Smart Burn Engine execution in September 2026

Blocks 25,878,705-26,093,737 (2026-09-01 00:00:00 UTC → 2026-09-30 23:59:59 UTC), Splitter `Kick` joined to Flapper `Exec` by transaction.

| Regime (burn / hop) | From | To | Kicks | USDS in | → Flapper | → USDS farm | SKY bought | VWAP |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 55% / 3,748 s | 2026-09-01 00:38:11 UTC | 2026-09-13 12:26:11 UTC | 271 | 1,626,000 | 894,300 | 731,700 | 13,441,363.22 | 0.066533 |
| 55% / 2,504 s | 2026-09-13 13:08:11 UTC | 2026-09-30 23:43:23 UTC | 575 | 3,450,000 | 1,897,500 | 1,552,500 | 27,106,218.45 | 0.070002 |
| **month** | | | **846** | **5,076,000** | **2,791,800** | **2,284,200** | **40,547,581.67** | **0.068852** |

**Burn attribution** — buys executed from the TMF's first cast (2026-08-17 14:02:23 UTC) are split per the regime they ran under: burn leg = 10% ÷ that regime's `splitter.burn` (10%/55% today), the rest to SKY stakers. Earlier buys (legacy 100% engine) went to the treasury un-attributed (BA Labs convention, t/28153).

| Line | Value |
|---|---:|
| Kicks since the TMF took effect | 846 |
| USDS spent in the window | 2,791,800.00 USDS |
| SKY bought in the window | 40,547,581.67 SKY |
| &nbsp;&nbsp;to SKY stakers (45%/55%) | 33,175,294.09 SKY |
| &nbsp;&nbsp;**to burn (10%/55%)** | **7,372,287.58 SKY** |
| *Alternative reading — 10/55 of every buy in the month (TMF sheet)* | *7,372,287.58 SKY* |

**Parameter changes observed in the month**

| When | Block | Contract | Parameter | Value | Tx |
|---|---:|---|---|---:|---|
| 2026-09-13 12:42:11 UTC | 25,968,583 | MCD_VEST_SKY_TREASURY | vest.yank (id) | 16 | `0xcd57535f…` |
| 2026-09-13 12:42:11 UTC | 25,968,583 | MCD_VEST_SKY_TREASURY | vest.init (id) | 17 | `0xcd57535f…` |
| 2026-09-13 12:42:11 UTC | 25,968,583 | REWARDS_DIST_LSSKY_SKY | vestId | 17 | `0xcd57535f…` |
| 2026-09-13 12:42:11 UTC | 25,968,583 | MCD_SPLIT | hop | 2504 | `0xcd57535f…` |
| 2026-09-13 12:42:11 UTC | 25,968,583 | REWARDS_LSSKY_USDS | rewardsDuration | 2504 | `0xcd57535f…` |

**SKY farm top-ups** — 5 distributor pulls, 36,166,499.78 SKY moved Pause Proxy → REWARDS_LSSKY_SKY.

| When | Block | SKY | Tx |
|---|---:|---:|---|
| 2026-09-07 11:16:47 UTC | 25,925,122 | 7,492,391.17 | `0x56b696c3…` |
| 2026-09-13 12:42:11 UTC | 25,968,583 | 6,524,101.82 | `0xcd57535f…` |
| 2026-09-13 12:44:35 UTC | 25,968,595 | 2,652.01 | `0x309a0b56…` |
| 2026-09-20 11:46:11 UTC | 26,018,514 | 11,073,898.39 | `0x179d53da…` |
| 2026-09-27 10:47:23 UTC | 26,068,291 | 11,073,456.39 | `0xed9e2a01…` |

## ④ On-chain state at month-end (block 26,093,737, 2026-09-30 23:59:59 UTC)

| Contract | Parameter | Value |
|---|---|---:|
| MCD_KICK | kbump | 6,000 USDS |
| MCD_KICK | khump (gate offset) | -200,000,000 USDS |
| MCD_SPLIT | hop | 2,504 s |
| MCD_SPLIT | burn | 55% |
| MCD_SPLIT | zzz (last kick) | 2026-09-30 23:43:23 UTC |
| REWARDS_LSSKY_USDS | rewardsDuration | 2,504 s |
| REWARDS_LSSKY_USDS | rewardRate | 1.078275 USDS/s |
| REWARDS_LSSKY_USDS | totalSupply (staked SKY) | 8,594,571,598 |
| REWARDS_LSSKY_SKY | rewardsDuration | 604,800 s |
| REWARDS_LSSKY_SKY | rewardRate | 18.416516 SKY/s |
| REWARDS_LSSKY_SKY | totalSupply (staked SKY) | 8,889,947,533 |
| REWARDS_DIST_LSSKY_SKY | vestId | 17 |
| REWARDS_DIST_LSSKY_SKY | lastDistributedAt | 2026-09-27 10:47:23 UTC |
| MCD_VEST_SKY_TREASURY | stream 17: bgn → fin | 2026-09-13 12:42:11 UTC → 2026-12-12 12:42:11 UTC |
| MCD_VEST_SKY_TREASURY | stream 17: tot / rxd | 143,208,393 / 22,150,007 SKY |
| MCD_VEST_SKY_TREASURY | cap | 70.7305 SKY/s |
| MCD_PAUSE_PROXY | SKY balance | 35,222,915 SKY |
| MCD_VAT / MCD_VOW | vat.dai(vow) - vat.sin(vow) | -56,449,234 USDS |
| MCD_VAT / MCD_VOW | vat.dai(vow) - (sin - Sin - Ash) | 171,746,388 USDS |
| USDS / DAI | totalSupply | 6,686,161,482 / 4,582,001,683 |

**APY snapshot** — this cycle's annualised rewards over the month-end staked balances, TWAP as the SKY price (projection, not a realised yield).

| Farm | Annual rewards | Staked | APY |
|---|---:|---:|---:|
| USDS farm | 31,613,646 USDS | 8,594,571,598 SKY (≈ 591,706,478 USDS) | 5.34% |
| SKY farm | 459,190,072 SKY | 8,889,947,533 SKY | 5.17% |

## Cross-checks

| Check | Computed | Published / reference | |
|---|---:|---:|:-:|
| Step 0 SNR vs settlements/sky_total | 14635947 | 14635947 | ✓ |

*Sources: TMF Configurations (forum.skyeco.com/t/28153) · Sky Atlas A.2.3 · dss-flappers (Kicker / Splitter / FlapperUniV2SwapOnly) · on-chain via HyperSync (events) and ETH_RPC (state). Open methodology questions in docs/tmf/README.md.*
