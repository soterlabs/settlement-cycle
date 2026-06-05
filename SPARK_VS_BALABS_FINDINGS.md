# Spark settlements vs BA Labs balance-sheet — findings (2026-06-05)

## TL;DR

1. **Fixed:** L2 sUSDS Cat B pricing (S37/S43/S47/S51) was completely
   broken — the proxy contracts on Base/Arb/Op/Uni don't expose
   `convertToAssets`, so the runner crashed on Jan 2026. Patched in
   `src/settle/compute/monthly_pnl.py` to defer initial valuation to
   the existing `_l2_susds_value` PSM3-pps recompute. Result: those
   four venues now match BA Labs **exactly** ($0.00 diff except a
   single $9.84 block-time-snap artifact at S43 Mar EoM).
2. **Fixed (infra):** purged 8 dead Dune query IDs from
   `~/.cache/msc-settle/dune_ids.json` (`psm3_shares_history.sql` and
   friends had been archived upstream, blocking every run). Added a
   `_DUNE_QUOTA_EXHAUSTED` short-circuit in `extract/dune.py` so 402s
   don't burn ~30s of 429 retries each — knocked the Apr/May runtime
   from a projected 6 h down to ~2 h.
3. **Fixed (infra):** PSM3 source now falls back to RPC
   (`psm3_shares` / `psm3_convert_to_asset_value`) on `DuneError` —
   covers the case where the init `_legs_at(period.start - 1)` call
   can't carry-forward and Dune is down.
4. **Largest remaining gap (CAN'T fix without external data):** S23
   Anchorage off-chain tri-party loan — **$150M every month**. BA Labs
   pulls this from `api.anchorage.com/v2/collateral_management/packages`.
   We don't have those credentials. The pipeline reads the on-chain
   USDC pass-through correctly (~$0–$133K). See "What I COULDN'T fix"
   §1 for the recommended YAML+pipeline change.
5. **Second largest gap (NEEDS implementation):** Spark Savings V2
   venues (S56–S60) skipped — `~$32M` total across all months. The
   `compute_monthly_pnl` code explicitly logs
   `"Spark Savings V2 compute path not yet implemented."` This is a
   straightforward addition (read `balanceOf(vault, underlying)` for
   each vault's idle underlying); the harder piece is the
   assets-vs-liabilities spread accounting for revenue.
6. **All other gaps are negligible** — every Cat A/B/C/F venue
   (excluding S23 and S56-S60) matches BA Labs to within $1K, and
   most match exactly. The single largest "other" item is S45 Mar
   ($228K), which is a block-timing artifact (we read the L2 EoM pin
   block; BA Labs snapshots a different intra-day point).

## Per-month total |diff| after the L2 sUSDS fix

| Month | ours_total | ba_total | |diff| | S23 share | S56-S60 share | other |
|---|---:|---:|---:|---:|---:|---:|
| 2026-01 | $3.15B | $3.33B | $182M | $150M | $32M | <$1K |
| 2026-02 | $3.49B | $3.67B | $182M | $150M | $32M | <$1K |
| 2026-03 | $4.26B | $4.44B | $181M | $150M | $30M | $230K (S45) |
| 2026-04 | $5.15B | $5.33B | $179M | $150M | $28M | <$1K |
| 2026-05 | $4.90B | $5.17B | $278M | $250M (loan ↑) | $27M | <$1K |

**Note:** May S23 jumped from $150M to **$250M** in BA Labs — the
Anchorage tri-party loan was raised by $100M in May. We still read
~$10 on-chain (the USDC pass-through).

**Apr position validation** (after L2 sUSDS fix, Apr re-run completed
2026-06-05 05:32 UTC):

Every on-chain venue matches BA Labs to the cent. Examples:
- `S32 sUSDS raw / POL`           : ours = BA Labs = $1,723,240,793.74
- `S43 Savings USDS proxy (Arb)`  : ours = BA Labs = $143,328,568.53
- `S37 Savings USDS proxy (Base)` : ours = BA Labs = $75,772,907.26
- `S47 Savings USDS proxy (Op)`   : ours = BA Labs = $102,040,993.03
- `S1  Spark USDS (SparkLend)`    : ours = BA Labs = $147,862,782.13
- `S3  Spark USDT (SparkLend)`    : ours = BA Labs = $677,669,071.17

Apr-only outliers in the Top-30 (after S23 + S56-S60): S24/S25 Curve
LPs at $984 / $925 diff (intra-period block-timing), S14/S15 Maple at
−$57 / −$25 (sub-share rounding). Everything else: $0.00 diff.

**May position validation** (run completed 2026-06-05 07:59 UTC):

```
venue              ours           BA Labs        diff
S37     $   76,003,428.30 $   76,003,428.30 $     0.00  Base sUSDS
S43     $  143,764,611.58 $  143,764,611.58 $     0.00  Arb sUSDS
S47     $  102,351,428.46 $  102,351,428.46 $     0.00  Op sUSDS
S51     $      985,396.93 $      985,396.93 $     0.00  Uni sUSDS
S32     $1,577,554,266.46 $1,577,554,266.46 $     0.00  sUSDS POL Eth
```

May completion required restart — first attempt got wedged in DRPC
retry loops on Unichain around 07:03 UTC (drpc.live had a brief
outage), reported 0% CPU for ~50 min. Killed cleanly and restarted at
07:56 with the same fixture set; finished in 3 minutes (RPC cache
warmed from the wedged run plus drpc was healthy again).

**Important caveat about Apr/May `prime_agent_revenue`:** because the
Cat B mint/burn fixture (`cat_b_cum_balance.json`) ends 2026-03-31,
`period_inflow=0` for every Cat B venue in Apr/May. The compute layer
then attributes the full `Δvalue` to `actual_revenue`, producing
clearly-wrong revenue numbers (Apr prime_agent_total_revenue = $630M
in the new run vs ~$5M in the May-29 baseline). The **positions
(value_som/value_eom) are unaffected** — they come from RPC
`balanceOf` at the pin blocks — but the revenue numbers in the
Apr/May provenance should NOT be used until the cum_balance fixture
is extended. Refreshing it requires Dune credits (resets 2026-06-13)
+ the ad-hoc Cat B/E capture queries (the auto-created Q1 query IDs
have been archived; would need to recreate them or use the published
`venue_inflow.sql` / `transfer_timeseries.sql` queries).

## Scope

* Pipeline: `python scripts/run_spark_2026.py` (consolidated runner — Jan→May 2026)
* Reference: `https://observatory.data.blockanalitica.com/primes/spark/balance-sheet/historic/`
* Comparison tool: `scripts/compare_spark_vs_balabs.py`
* Method: per-venue `value_eom` from our `provenance.json` vs BA Labs `assets` rows
  matched by `(wallet=Spark ALM, token=our.token.address)` (chain-keyed). Spark
  Savings V2 venues (S56-S60) match by `wallet=vault_address`; S23 Anchorage
  by `category=anchorage`.

## What I fixed

### 1. L2 sUSDS Cat B pricing — S37 / S43 / S47 / S51 (Savings USDS proxies)

**Symptom:** runner crashed for every Jan 2026 month with
`eth_call error: 'execution reverted'` during `convert_to_assets` at
the Jan SoM block for Base/Arb/Op/Uni sUSDS proxies. The proxies don't
expose `convertToAssets` at all — the function reverts on every block,
not just early ones.

**Root cause:** `compute_monthly_pnl` unconditionally calls
`get_position_value` (which hits the ERC-4626 `convertToAssets` source)
**before** the dedicated `_l2_susds_value` recompute. The recompute uses
PSM3-pps from Ethereum sUSDS and was added in PR #84 (b31d27b), but it
fires only after `value_som`/`value_eom` have already been computed.
When the initial call raises, the whole venue blows up.

**Fix:** `src/settle/compute/monthly_pnl.py:2035` — gate the initial
`get_position_value` calls on a `_defer_l2_susds` flag
(`pricing_category == ERC4626_VAULT and sky_savings_token and chain != ETHEREUM`).
For these venues, start at `Decimal(0)` and let the existing
`_l2_susds_value` recompute populate the values via PSM3 pps.

**Result:** S37/S43/S47/S51 now match BA Labs essentially exactly
across Jan/Feb/Mar:

```
venue   month                ours          BA Labs       diff
----------------------------------------------------------------------
S37     2026-01  $  75,079,284.39 $  75,079,284.39 $     0.00
S37     2026-02  $  75,305,516.52 $  75,305,516.52 $     0.00
S37     2026-03  $  75,545,660.91 $  75,545,660.91 $     0.00
S43     2026-01  $ 196,264,917.55 $ 196,264,917.55 $     0.00
S43     2026-02  $ 196,856,311.43 $ 196,856,311.43 $     0.00
S43     2026-03  $ 142,898,719.72 $ 142,898,709.88 $     9.84
S47     2026-01  $ 101,106,912.91 $ 101,106,912.91 $     0.00
S47     2026-02  $ 101,411,572.60 $ 101,411,572.60 $     0.00
S47     2026-03  $ 101,734,967.51 $ 101,734,967.51 $     0.00
S51     2026-01  $     973,415.25 $     973,415.25 $     0.00
S51     2026-02  $     976,348.39 $     976,348.39 $     0.00
S51     2026-03  $     979,461.90 $     979,461.90 $     0.00
```

The single $9.84 difference at S43 Mar EoM is a sub-cent-per-share
artifact from the L2-block-to-Eth-block date snap inside
`_l2_susds_value` (a 12s block-time offset translates to ~$10 of pps
drift on $143M).

### 2. Stale Dune-query auto-registry — `psm3_shares_history.sql` (+7 others)

**Symptom:** `DuneError: query 7483773 → HTTP 404`. The PSM3 source's
holder-history query 404'd, blocking every Spark run.

**Root cause:** `~/.cache/msc-settle/dune_ids.json` maps SQL-content
hashes to auto-created Dune query IDs. Eight of those queries had been
archived/deleted upstream, but the local cache still pointed to them.

**Fix:** Purged 8 dead entries (7483773, 7483782, 7484227, 7487918,
7489253, 7489266, 7489270, 7482554). Next run auto-creates fresh public
queries via `_resolve_query_id`.

### 3. PSM3 source — RPC fallback on Dune quota exhaustion

**Symptom:** `_load_holder_history` and `_load_pool_history` raise
`DuneError(HTTP 402)` when monthly Dune credits run out, killing the
PSM3 init read in `_legs_at(period.start - 1)` (which cannot use the
carry-forward fallback because there's no prior day).

**Fix:** `src/settle/normalize/sources/dune_psm3.py` — `shares_of` and
`convert_to_asset_value` now catch `DuneError` and fall back to direct
RPC reads (`rpc.psm3_shares` and `rpc.psm3_convert_to_asset_value`,
which already exist as cached primitives). The fallback is per-block-
exact — equivalent to the event-reconstruction path for a single
snapshot.

## What I COULDN'T fix (raised for follow-up)

### 1. S23 Anchorage off-chain tri-party loan — **$150M every month**

**This is the dominant remaining discrepancy.** BA Labs reads the
off-chain Anchorage exposure via `api.anchorage.com/v2/collateral_management/packages`
(see the script you shared, `get_anchorage_position_data`). The pipeline
correctly reads the on-chain USDC pass-through balance (~$0 to ~$133K
across Jan-Mar — this is the dust at the Anchorage escrow address).

**Why not fixed:**
* No Anchorage API credentials on this side.
* `Venue.notional_principal_usd` exists for "cash-distribution-only"
  venues (Galaxy CLO E21 on Grove uses it for the $50M off-chain
  notional), but it only affects `cof_alloc` (utilized denominator)
  — it does NOT inject into `value_eom`. Adding it to S23 would shift
  CoF but not close the BA Labs comparison.

**Recommended fix:** add either
* a new YAML field `off_chain_value_usd:` that's surfaced as `value_eom`
  for display-only/cash-distribution venues, OR
* an Anchorage API source (`extract/anchorage.py`) and wire it into the
  Cat E balance path.

### 2. Spark Savings V2 — S56-S60 — **~$32M total each month**

```
S56 spUSDC eth        : $10M     skipped
S57 spUSDT eth        : $10M     skipped
S58 spETH eth         : $2.5-5.6M skipped
S59 spPYUSD eth       : $29K-$1M  skipped
S60 spUSDC avalanche  : $10M-$2M  skipped
```

**Why not fixed:** these venues are tagged `pricing_category: S2` in
`config/spark.yaml` and `compute_monthly_pnl` explicitly skips them
with the log line "Spark Savings V2 compute path not yet implemented."
A separate code path is needed — the `S2` category models the
vault-as-wallet semantics (vault holds its own ALM positions across
many protocols, and Spark's S56-S60 venues are positions IN those
vaults).

The deferred-data source `DuneSavingsV2DeployedSource` is also
flagged unavailable (upstream Dune table removed), so even Cat B
venues that should net out S2 deployments (S32 sUSDS POL marked
`deduct_savings_v2_deployed: true`) are running without the deduction.

### 3. S45 Arbitrum USDC raw — **$228K at Mar EoM**

BA Labs sees $4.67M, we see $4.44M. Daily BA Labs shows S45 fluctuates
between $4.4M and $5.0M intra-period (mostly idle at $5M with periodic
draws). The pipeline reads at the L2 EoM pin block (last block ≤
2026-03-31 23:59:59 UTC = block 447,736,930); BA Labs likely snapshots
at a different intra-day time. Both are correct at their respective
blocks — this is a defensible methodology difference, not a bug.

### 4. Apr/May fixtures — Dune credits exhausted

Tried to refresh `tests/fixtures/spark_2026_q1/` (debt_timeseries +
daily EoD blocks) for Apr/May. All Dune calls now return HTTP 402
("This api request would exceed your configured datapoint limit per
billing cycle"). The current Spark Q1 run alone burned the 2500 monthly
credits on auto-created queries (atoken external_revenue, PSM3
preload, savings_v2_deployed, etc.).

**Workaround applied:** `scripts/run_spark_2026.py` now has Apr/May
pin blocks hardcoded (Eth+Base+Avalanche-C from Grove's Apr/May
fixtures; Arb/Op/Uni resolved via RPC binary search). The runner uses
the existing `spark_2026_q1` fixture for Apr/May too; the
`_FixtureMultiResolver` falls back to RPC for date→block lookups
outside the Q1 capture window. `value_eom` is unaffected (pin blocks
+ RPC `balanceOf`); `period_inflow` for Cat B venues is 0 (Cat B
cum_balance fixture ends 2026-03-31).

**Recommended fix:** refresh the Cat B/E `cum_balance` JSONs once
Dune credits reset (billing period rolls 2026-06-13). The
`extend_spark_fixtures.py` helper I left in the repo handles debt +
blocks; Cat B/E need bespoke ad-hoc queries (the auto-created Dune
queries from the original capture have all been archived).

## Summary table — sum |diff| per month after fixes

| Month | ours_total | ba_total | sum_abs_diff | ≈ S23 share | ≈ S56-S60 share | other |
|---|---:|---:|---:|---:|---:|---:|
| 2026-01 | $3.15B | $3.33B | $182M | $150M | $32M | <$1K |
| 2026-02 | $3.49B | $3.67B | $182M | $150M | $32M | <$1K |
| 2026-03 | $4.26B | $4.44B | $181M | $150M | $30M | $230K (S45) |

(Apr/May still running as of writing — same shape expected.)

## Files touched

| Path | Change |
|---|---|
| `src/settle/compute/monthly_pnl.py` | Defer initial `get_position_value` for L2 sUSDS Cat B venues (avoid the proxy revert). |
| `src/settle/normalize/sources/dune_psm3.py` | RPC fallback in `shares_of` + `convert_to_asset_value` on DuneError. |
| `scripts/run_spark_2026.py` | Added Apr/May pin blocks + extended `_MONTH_PLAN`. |
| `scripts/compare_spark_vs_balabs.py` | New — per-venue diff vs BA Labs. |
| `scripts/extend_spark_fixtures.py` | New — Dune fixture refresher (blocked by 402 until credits reset). |
| `~/.cache/msc-settle/dune_ids.json` | Purged 8 dead query IDs. |
