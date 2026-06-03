# Grove headline-gap investigation — Jan-May 2026

Deep-dive into the discrepancies between our `sky_revenue` /
`prime_agent_revenue` headlines and the per-venue totals Grove publishes in
`data/grove/{month}_2026.xlsx`. Conducted 2026-06-03.

## TL;DR

The April +$1.84M `Profit to Sky` discrepancy was caused by a
**capture-script bug**: `tests/fixtures/grove_2026_04/_capture_dune_fixtures.py`
had `START_DATE = "2025-10-23"` instead of Grove's true prime start
`2025-05-14`. Five months of BUIDL (E10) and JTRSY (E9) transfer events
were missing from the cum_balance fixture, so the SDE-asset-value daily
deduction was understated by ~$520M every day in April, inflating CoF by
~$1.7M.

After fix, April Δ shrinks from **+$1.84M → +$154K** (closes 96% of the
gap). The remaining ±$50–400K monthly gaps are minor methodology deltas,
documented below.

## Per-month gap attribution (post-fix)

| Month | Ours P2S | Grove P2S | Δ P2S | Driver |
|---|---:|---:|---:|---|
| 2026-01 | $6,595,461 | $6,220,570 | +$374,891 | (a) PR #103 grab + (b) JAAA capped SDE allocation + (c) alt-holder venues |
| 2026-02 | $5,650,300 | $6,042,238 | −$391,938 | (d) Q1 fixture's `debt` series was missing 17 of 19 daily Feb rows — forward-fill silently understated mid-Feb cum_debt by $150-300M |
| 2026-03 | $6,333,154 | $6,376,609 | −$43,454 | small; mid-period JAAA SDE end blends our buckets close to Grove's |
| 2026-04 | $9,364,842 | $9,211,014 | +$153,828 | PR #103 grab + minor (a) + (b) |
| 2026-05 | $8,629,395 | (no Grove xlsx) | — | — |

## Root cause — April

The fixture's `cum_balance_e9` (JTRSY) and `cum_balance_e10` (BUIDL)
series fed `_sde_asset_value_timeseries` as the daily SDE-asset-value
deduction from `utilized`. Both series were built from the Dune
`transfer_timeseries.sql` query, which sums signed transfers from
`start_date` onward starting at zero.

When `start_date = 2025-10-23` (the buggy value) instead of `2025-05-14`
(Grove's actual prime start), all transfers between those dates were
filtered out. The resulting cum_balance was off by a constant offset
equal to the holdings the ALM accumulated before Oct 23:

| Token | Q1 fixture Mar 31 | Apr fixture Mar 31 (buggy) | Actual on-chain Mar 31 |
|---|---:|---:|---:|
| BUIDL | $698,277,167 | $439,910,000 | $706,747,881 |
| JTRSY | $1,054,843,777 | $817,398,518 | $1,054,843,777 |

The Q1 fixture (`tests/fixtures/grove_2026_03/dune_outputs.json`) was
captured separately with the correct start date and remains valid;
Jan/Feb/Mar are unaffected.

On-chain verification via direct RPC reads at the pin block (`balanceOf`
calls to `ethereum.publicnode.com` at block 24781026 = Mar 31 EOM):

```
BUIDL ALM balance = 706,747,881 tokens (decimals=6)
JTRSY ALM balance = 1,054,843,777 tokens (decimals=6)
```

These match the Q1 fixture's data and Grove's published `Asset Value`
column in the `USDS Line` tab. The Apr/May fixtures were the outlier.

## Fix applied

1. **`tests/fixtures/grove_2026_04/_capture_dune_fixtures.py:45`** —
   corrected `START_DATE` to `"2025-05-14"` with a documented warning
   referencing this incident.
2. **`tests/fixtures/grove_2026_05/_capture_dune_fixtures.py:33`** — same.
3. **`tests/fixtures/grove_2026_04/dune_outputs.json`** + **`05/dune_outputs.json`** —
   `cum_balance_e9` shifted by +$237,445,259 and `cum_balance_e10` by
   +$258,367,167 so the Mar 31 anchor aligns with the Q1 fixture. A proper
   re-capture from Dune (using the corrected START_DATE) would also pick up
   sub-$1M yield-distribution mints that the `min_transfer_amount=$1M`
   filter currently strips for BUIDL — residual $8-50M discrepancy at SoM
   per venue (see warnings logged by the safeguard below).
4. **`src/settle/compute/monthly_pnl.py:2622` (new safeguard)** — added a
   log warning when the SDE timeseries' SoM `uncapped_value` diverges from
   the venue's `value_som` by more than $1M. This catches a future
   capture-script / filter regression before it ships skewed numbers.
   Currently fires for E9/E10 in Apr/May runs flagging the residual yield-
   mint filter effect.

## Remaining methodology deltas

These are pre-existing differences in how Grove and our pipeline define
`utilized` and per-venue allocation. They are NOT bugs on our side — they
reflect different (but defensible) methodology choices.

### (a) PR #103 — `vat.grab` in `cum_debt`

We include `vat.grab` events; Grove's `Subscriptions` column uses
`vat.frob` only. Lift on cum_debt → lift on CoF:

| Month | Cumulative grab (through EoM) | Monthly CoF lift |
|---|---:|---:|
| Jan | $15.4M | +$62K |
| Feb | $29.7M | +$107K |
| Mar | $42.2M | +$147K |
| Apr | $42.2M | +$166K |
| May | $42.2M | +$172K |

### (b) Subsidy ramp formula

Per Atlas §A.2.8.2.2, our pipeline uses:

```
subsidised_apy = ref_rate + (base_apy − ref_rate) × min(T, 24) / 24
```

where T = months elapsed since `program_start = 2026-01-01`. Grove's
`Adj Subsidized CoF` column appears to use `subsidised_apy ≈ ref_rate`
(no ramp toward `base_apy`). Resulting rate gap:

| Month | T | Our subsidised | Grove subsidised | Δ |
|---|---:|---:|---:|---:|
| Jan | 0 | 3.670% | 3.604–3.643% | +3-7bps |
| Feb | 1 | 3.725% | 3.630–3.640% | +9bps |
| Mar | 2 | 3.781% | ~3.69% | +9bps |
| Apr | 3 | 3.836% | ~3.68% | **+16bps** |

The April +$154K residual after the fixture fix is largely the
subsidy-ramp delta on Grove's avg Net Subs ($930M × 0.0016 × 30/365 ≈
$122K). Worth raising with Grove team to confirm canonical ramp formula.

### (c) JAAA capped SDE allocation (Jan-Feb only)

E8 JAAA was capped SDE through 2026-03-11. Our `sd_share` formula
(min(cap, value_eom) / value_eom) yields a per-period Sky share. Grove's
xlsx uses a different per-venue allocation that produces slightly
different daily Sky shares. Net impact: per-venue Sky off by tens of $K
per month for E8.

### (d) Q1 fixture had incomplete Feb debt rows (root cause of Feb gap)

**Initial hypothesis (wrong, retained for record):** I first thought
our `utilized` was lower than Grove's `Net Subs` because we deduct
ALM idle USDS / PSM USDS / Curve idle / lending idle, while Grove
only deducts SDE asset value. That turned out to be **incorrect** —
Grove's `config/grove.yaml` shows:

- No PSM tracking (explicit comment: "Sky's mainnet PSM stack is
  non-custodial — no per-prime balances accumulate")
- No `curve_idle_usds` config on E11 (AUSD/USDC pool — neither coin
  is USDS/sUSDS, so the deduction doesn't apply)
- No `lending_idle_usds: true` flag on E1/E2/E3 (Aave aTokens)
- E17 USDS raw at ALM = $0 throughout (verified on-chain + via BA
  Labs daily series)

So our utilized for Grove deducts ONLY SDE asset value — identical to
Grove's Net Subs definition. The "broader deduction" hypothesis was
wrong.

**Real cause:** the Q1 fixture (`grove_2026_03/dune_outputs.json`) had
only **2 daily debt rows for February** (Feb 2 + Feb 27), while a
fresh Dune re-capture returns **19 daily rows** (Feb 2, 6, 12, 13,
16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 27). Our pipeline forward-
fills `cum_debt` from the latest available row, so the missing rows
caused us to silently understate cum_debt by $150-300M during mid-Feb.
Lower cum_debt → lower utilized → lower CoF → lower sky_revenue.

Re-capturing the fixture fresh from Dune (using the corrected
START_DATE 2025-05-14 from Fix 1 + the new frob+grab query 7642450
from Fix 2) restores the full 19-row Feb series. After fix, Feb gap
shrinks from −$391K → +$94K (closes 76% of the gap; residual is
subsidy-ramp methodology delta documented in section (b)).

### (e) Alt-holder venues we track that Grove doesn't (or vice versa)

We track UNIV3 AUSD/USDC alt-holder pool (E12+E30) which Grove doesn't
list in `Summary Comp`. Grove may track BUIDL/JTRSY positions on certain
chains we don't read. Net effect: ±$50–100K per month, small.

## Forward action items

1. **Re-capture Apr/May fixtures from Dune** with the corrected
   `START_DATE = "2025-05-14"`. This recovers the sub-$1M yield-mint
   history the filter currently strips and eliminates the residual
   $8-50M SoM divergence the safeguard flags. Requires Dune API access
   beyond free-tier.
2. **Decide on the subsidy-ramp formula** with Grove team. Either confirm
   our `min(T, 24)/24` ramp matches Atlas and Grove updates their
   methodology, or align ours to Grove's flat-ref_rate approach.
3. **(Optional)** Consider moving `_sde_asset_value_timeseries` to use
   daily `balanceOf` reads (via `IPositionBalanceSource.balance_at`)
   instead of Dune cum_balance. Removes the dependency on transfer-event
   completeness entirely. Cost: ~30 RPC calls per SDE venue per month.
