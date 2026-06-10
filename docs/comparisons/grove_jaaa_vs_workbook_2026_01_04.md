# JAAA (E8 / E20) — settlement pipeline vs Grove PnL workbook, Jan–Apr 2026

Comparison of the JAAA position's Grove-side and Sky-side profit between
our settlement artifacts (`settlements/grove/2026-{01..04}/`) and the
Grove team's monthly PnL workbooks (`data/grove/{jan,feb,mar,apr}_2026.xlsx`,
sheet `Asset-Level PnL`, aggregation block; March splits JAAA_ETH into
`JAAA_ETH_Grove` / `JAAA_ETH_Sky` columns).

**Verdict: no material gap.** Largest 4-month divergence is −$35.3K on the
Sky side — below the $50K materiality bar agreed for Jan–Apr — so the
methodology stays as-is and the differences are documented here instead.

## Numbers

Sky revenue from JAAA_ETH SDE (ours = E8 `sd_revenue`; workbook =
"Revenue to Sky Due to Direct Exposures"):

| Month | ours | workbook | Δ |
|---|---:|---:|---:|
| Jan | 1,429,870.18 | 1,432,988.25 | −3,118.07 |
| Feb | 991,827.49 | 997,685.56 | −5,858.07 |
| Mar | −477,414.38 | −451,060.49 | −26,353.89 |
| Apr | 0.00 | 0.00 | 0.00 |
| **Σ** | **1,944,283.29** | **1,979,613.32** | **−35,330.03** |

Grove gross revenue from JAAA_ETH (ours = E8 `revenue` = actual −
sd_revenue; workbook = "Remaining Value Available"):

| Month | ours | workbook | Δ |
|---|---:|---:|---:|
| Jan | 933,299.18 | 930,126.75 | +3,172.43 |
| Feb | 396,689.67 | 390,895.44 | +5,794.23 |
| Mar | 0.00 | −7,238.26 | +7,238.26 |
| Apr | 590,469.81 | 590,462.00 | +7.81 |
| **Σ** | **1,920,458.66** | **1,904,245.93** | **+16,212.73** |

JAAA_AVAX (E20, no SDE — Grove keeps 100%): matches within ~$100/month
(Σ Δ = +$126.92 over four months; the workbook rounds daily values).

## The three methodology differences

### 1. SDE split granularity — EoM-locked capped share vs daily pro-rata

Ours: E8 carries a **capped SDE** ($325M, `config/sky_direct_exposures.yaml`)
and the split is **EoM-locked**: `sd_share = min(cap, value_eom) / value_eom`
computed once per month (`_capped_sd_revenue_eom_locked`). January's heavy
redemptions ($300M out) leave value_eom ≈ $454M → sd_share 60.5%; by March
the remaining $128M is fully under the cap → sd_share = 1 and the whole
March venue P&L (−$477K) books to Sky.

Workbook: tracks **Sky Value / Grove Value daily** (sheet `JAAA_ETH
Allocation`) and attributes each day's Δvalue pro-rata to that day's
slices. Sky's slice exited Mar 9–11 at a realized −$451,060; Grove's
residual slice lost −$7,238 over the rest of March.

The March divergence (−$26.4K Sky-side, +$7.2K Grove-side) is exactly
this: a month containing a large mid-month SDE exit splits differently
under an EoM-locked share than under daily slicing. In months without
composition changes the two agree to <$6K.

### 2. Redemption valuation — Centrifuge Withdraw events vs daily NAV

March total venue yield: ours −$477,414 vs workbook −$458,299
(Δ −$19.1K). We price flows off the Centrifuge vault's
`Withdraw(assets)` events (exact USDC received) plus EoM MtM on the
pricePerShareFeed; the workbook marks daily NAV and realizes the exit at
its daily price marks. The Δ is a timing/mark difference on the $327M
redemption, not a missing flow.

### 3. Cost of funds — portfolio-level vs per-venue

The workbook charges CoF per asset ("Total Cost of Funds" per ticker,
giving "Net Profit to Grove" per venue). Our settlement deliberately
charges BR on **utilized at the prime level** and does not attribute CoF
per venue — so our per-venue `revenue` compares to the workbook's
*gross* ("Remaining Value Available"), never to its *net* row. Total CoF
reconciles at the prime level (see `docs/GROVE_HEADLINE_GAP_ANALYSIS.md`).

## If this ever becomes material

The trigger would be another month with a large mid-month change in the
SDE slice (entry, exit, or cap change). The fix direction is known:
promote `_capped_sd_revenue_eom_locked` to a daily-integrated split using
the same `_sde_asset_value_timeseries` machinery that already tracks the
in-flight window (burn_date → usdc_settlement_date). Not done now: the
remaining JAAA SDE is closed (end_date 2026-03-12), so the change would
only re-attribute ±$33K between Sky and Grove retroactively.
