# MSC — Open Questions

Single source of truth for outstanding questions across the MSC settlement
pipeline. Grouped by recipient (**Grove team**, **Spark team**, **BA labs**)
and ordered by priority within each group:

- **P0** — material numerical gap in the current settlement output
- **P1** — methodology unknown that would shift numbers if confirmed/changed
- **P2** — sanity check / confirmation, no current numerical impact
- **P3** — future-proofing, operational, or dormant (venue holds $0)

Question IDs (G1, S6, B4, …) are stable and cross-referenced from `PRD.md §17`.
Last consolidated: 2026-05-04. Resolved questions (subsidised rate, PSM3
daily sampling, hardcoded EoM blocks, Foundation USDS, GACLO-1 valuation,
~$1.13M Sky-Share residual) are dropped here but tracked in `PRD.md §17`.

---

## Grove team

### P0 — material numerical gaps

#### G3. E1/E2 aHorRwa* off-pool yield channel — Merkl, Aave Horizon, or Janus?
Investigation 2026-05-02 / 2026-05-04: the Aave Horizon `liquidityIndex`
itself only grows ~0.87% APY for aHorRwaRLUSD, capturing only ~$67K of the
$514K yield Grove team's PnL workbook reports for E1 in Feb 2026. The
remaining ~$447K is **off-chain accrual** — Holdings sheet's `Rewards`
column grows ~$431K with `claimed` flat at $821K (no claim event). The
Grove address registry lists `MERKL_DISTRIBUTOR =
0x3Ef3D8bA38EBe18DB133cEc108f4D14CE00Dd9Ae`. Is Grove's `Rewards` column
sourced from (a) Merkl's `userRewards` API, (b) Aave Horizon's RWA-fund
accrual API, (c) Janus / Anemoy pool reporting, or (d) something else?
Once confirmed we'll add a new `IRewardsSource` and pipe through Cat C
revenue. Until then E1 revenue is under-counted by ~$430K/month.

### P1 — methodology unknowns affecting accuracy

#### G2. External ALM sources / off-chain yield distributors
`external_alm_sources` is empty in `config/grove.yaml` → revenue from idle
par-stables (E13–E17) = **$0**. Does Grove have any off-chain custodian
sending realized yield directly to the ALM proxy
`0x491edfb0b8b608044e227225c715981a30f3a44e` (RLUSD coupon distributions,
AUSD-issuer rebates, etc.)? If yes, we need the sender address(es).

#### G6. Chronicle adapter — pre-deployment silent fallback to const_one
E7 STAC, E8 JAAA, E9 JTRSY, E22 ACRDX use Chronicle NAV oracles. Several
were deployed mid-2025; reads at SoM blocks before deployment **revert**
and we silently fall back to `const_one` ($1). Without manual
`nav_overrides` per block, January Grove revenue would include a phantom
**$100M jump** on E7. Is `nav_overrides` an acceptable long-term
solution, or should we get the actual deposit-time NAV from
Securitize/Janus/Anemoy directly? The adapter itself should distinguish
"pre-deployment revert" from "real $1" — currently it doesn't.

#### G7. STAC (E7) NAV — Securitize programmatic feed
Securitize publishes NAV via API but no on-chain `convertToAssets` /
`asset()` (probed 2026-04-21 — reverts). We rely on Chronicle
`0x9d77…58b`; if Chronicle goes stale, fallback is const_one ($1). At
STAC's $1000-per-token denomination this is a **catastrophic** silent
failure mode. Does Securitize publish NAV anywhere we can consume
programmatically (REST API, IPFS, signed JSON)? Redstone has a feed at
`0xedc6…d7d` but our `NavOracleSource` doesn't speak Redstone yet.

### P2 — sanity checks / confirmations

#### G4. Sky Direct venue set re-confirmation
Per the Atlas spec: Treasury Bills on Eth (BUIDL/JTRSY/USTB) +
USDC in PSM3 non-Eth + USDT in sUSDS/USDT Curve. Grove's currently
flagged Sky Direct: **E9 JTRSY** + **E10 BUIDL** (driven by
`config/sky_direct_exposures.yaml`, the time-bounded SDE table; the
older per-venue `sky_direct: true` flag in `<prime>.yaml` is deprecated
and ignored by compute). Confirm no other Grove venue should be Sky
Direct as of today, and please flag this list for re-review whenever
the Atlas Sky Direct section changes.

#### G5. Subsidy reference rate — confirm Grove uses 3M T-Bill (not EFFR)
We've configured Grove with `ref_rate_kind: tbill_3m` per your guidance,
matching what the Feb 2026 PnL workbook empirically used (3.67–3.74%
range, vs EFFR's ~4.33%). Confirm this is the long-term spec, not just a
Feb-2026 implementation choice. Same question for Spark (currently using
EFFR per same guidance).

#### G1. Subproxy idle USDC — should it earn agent rate?
Grove's subproxy `0x1369…91Ba` holds ~$0.75M USDC. Today it does **not**
earn agent rate — only USDS/sUSDS sitting in the subproxy do. Is this
intentional, or should USDC parked at the subproxy during USDS↔USDC swap
windows also earn agent rate?

#### G12. AUSD par assumption (E11 Curve, E12 Uni V3)
E11 Curve AUSD/USDC LP = `balance_of(LP) × virtual_price × $1`; E12 Uni
V3 NFT positions priced with both coins at $1 par. Confirm AUSD is
treated as $1 par by Grove (we don't track AUSD/USDC depeg). Any AUSD
peg break would silently overstate value on E11/E12 by a few percent.

#### G13. E13–E18 idle ALM holdings — accounting confirmation
RLUSD/AUSD/USDC/DAI/USDS raw + sUSDS POL are tracked as Cat A (par) +
Cat B (sUSDS) venues. Confirm this matches Grove's internal accounting:
E17 USDS raw and E18 sUSDS POL both contribute to `Σ value` AND are
netted out of `utilized` (USDS in `cum_alm_usds`, sUSDS via the implicit
Cat B spread). No double-count, but worth a sanity check.

#### G14. Q1 2026 Grove venue revenue split — does it match?
March 2026 top contributors (post all 2026-05-02 fixes): E11 Curve LP +
E12 V3 LP +$7,738; E4/E5/E19/E23 Morpho 4626 +$34,634; E6 grove-bbqAUSD
+$40,044; E9 JTRSY (Sky Direct, capped) — Sky takes ~$580K; E10 BUIDL
— Sky takes ~$191K. Does this venue-by-venue split match Grove's
internal accounting?

#### G15. Cost-basis bias +0.63% for March 2026
Σ value $2.827B vs cum_debt $2.809B across 23 venues. The +0.63% is
largely E23 Steakhouse Prime Instant on Base (~$16M EoM, mid-March
creation, not yet fully tracked in cum_debt). Pre-E23 was +0.06%. Is
this expected (legitimate yield accrual that hasn't crystallised in
cum_debt) or noise we should investigate?

#### G16. Maker's official PSM term divergence
Our `utilized` formula subtracts PSM3 USDS-equivalent; Maker's official
`6954386_daily_utilized_usds` does not. For Grove the values match
because Grove holds $0 in PSM3. Confirm Grove never expects to park USDS
at PSM3 long-term.

#### G8. Centrifuge tranche tokens — backup NAV feed
Switched 2026-05-02 from Chronicle to Centrifuge `pricePerShareFeed`
(`0x4880…0B` for E8 JAAA, `0xFE69…77A` for E9 JTRSY) per Grove team's PnL
workbook. Does Centrifuge expose another backup (Pool Manager contract
or off-chain API) we could fall through to if `pricePerShareFeed` goes
down? ACRDX dropped −0.69% in March 2026 — silently falling through to
const_one would mask a real loss month.

### P3 — future-proofing / operational

#### G9. BUIDL-I (E10) — mint-pattern threshold confirmation
NAV pinned to $1; yield captured via the actual mint events (BlackRock
mints both capital subscriptions AND yield distributions to the ALM as
ERC-20 mints from `0x0`). We use a bimodal-histogram filter
(`min_transfer_amount_usd: 1000000`) to separate capital (≥$10M, large
round numbers) from yield (<$1M, dozens per period). Could the threshold
drift if BUIDL-I starts processing larger yield mints or smaller capital
subscriptions?

#### G10. Monad E25 grove-bbqAUSD — RPC archival window
Both Alchemy and drpc Monad endpoints expose ~3.8M-block archival
windows. SoM (12.6M blocks back) and EoM (5.9M back) are outside
available state, so historical `balanceOf` / `convertToAssets` reads
fail. Position is small (~$6.5M EoM, 0.23% of book). Does Grove have
access to a dedicated archival Monad node we could query? Alternative:
Dune-cum-balance × const-pps approximation.

#### G11. Aave aToken edge cases — V4 migration heads-up
`scaledBalanceOf × liquidityIndex` works for Aave V3 + SparkLend (current
E1/E2/E3 venues). Any plans to migrate to Aave V4 or another rebase-model
lending market? A migration mid-period would require a separate code
path. None of the current Grove venues trip this today.

---

## Spark team

### P0 — material numerical gaps


#### S6. spUSDC / spUSDT / spETH / spPYUSD — surplus formula (~ANSWERED via `dune.sparkdotfi.result_savings_v_2_deployment_metrics`)

S56–S60 in `config/spark.yaml` (~$2.3B+ TVL combined) are still skipped
in our compute, but the public table
`dune.sparkdotfi.result_savings_v_2_deployment_metrics` exposes the
formula as `(dt, token_symbol, total_amount, holding_amount,
deployed_amount, apr, borrow_cost)` per vault per day. Implied accounting:

- **vault assets** = `total_amount` = `holding_amount + deployed_amount`
- **vault liabilities to depositors** ≈ `total_amount × Π_d (1 + borrow_cost_d/365)`
  (TWA accrual at the savings rate)
- **daily Spark surplus** = `deployed_amount × (apr − borrow_cost) / 365`
  (the spread on deployed capital; idle holding doesn't earn or accrue
  liability)

**Refined Q (smaller):** confirm this interpretation, especially:
- Does `holding_amount` accrue NO liability to depositors (because it's
  not earning the savings rate either)?
- Is `apr` net of any vault-level performance fee, or do we need to
  apply that separately?
- For initial seed principal: `total_amount` includes Spark's seed
  capital, right? Then we don't need a separate seed-principal lookup.

We'll add the compute path once these three confirmations land.

### P1 — methodology unknowns affecting accuracy

#### S1. Confirm `2024-11-18` is Spark's billing anchor
That's the date of the first frob on `ALLOCATOR-SPARK-A`. If Sky bills
from a different anchor (e.g., earlier KYC date), `cum_debt` and
downstream `sky_revenue` would shift.

#### S4. Multi-chain ALM netting in sky_revenue
`compute_sky_revenue` currently nets only **USDS** at the Eth ALM/subproxy
+ PSM USDS-equivalent. PYUSD/USDC/USDT held at Eth ALM are NOT netted
(Spark pays base rate on them). Should ALM USDC on Base be netted as
"idle USDS-equivalent" since it was funded from Sky USDS that just got
swapped?

#### S7. Liabilities = sky_borrow + savings_v2_borrow (~CONFIRMED via `result_spark_sll_revenue_projection_raw_1`)

The projection table publishes both `sky_borrow_cost_proj_usd` and
`saving_v2_borrow_cost_proj_usd` as **separate** liability sources.
Combined with the per-protocol idle tables (every `result_spark_*_by_alm_proxy`
table has a `borrow_cost_apr` column representing what Spark pays on
that capital), our model should be:
```
total_liabilities = sky_debt × subsidised_BR + Σ_v vault_liability_v
```
where `vault_liability_v` is the borrow_cost term per source.

**Smaller Q:** does `sUSDS POL` (S32, ~$2.47B) count under
`sky_borrow_cost` (because it's USDS borrowed from Sky and parked in
sUSDS) or `saving_v2_borrow_cost` (because the underlying USDS earns SSR
which is the savings rate)? Both are theoretically valid framings.

#### S9b. Ethena S16 has Spark+Grove-shared accounting (NEW, from `result_spark_ethena_payout_apy`)

`result_spark_ethena_payout_apy` schema:
```
total_holdings, spark_cumulative_withdrawal,
grove_holdings, spark_holdings, spark_share,
usde_value, usde_withdrawal_value, susde_value, u_usde_value,
daily_usde_pay_value, daily_usde_pay_value_spark_share,
daily_actual_revenue, daily_br_cost
```
This says **Spark and Grove share the Ethena position**, with `spark_share`
as the apportionment factor. It also distinguishes between four
unstaking states (`usde / susde / u_usde / usde_withdrawal`).

Our pipeline treats S16 sUSDe as a flat Cat B venue with no
share-of-pool concept and no four-state lifecycle. **Q for Spark:**
- Confirm `spark_share` = on-chain Spark-balance / (Spark-balance + Grove-balance)
  at each `dt`?
- Are the `u_usde_value` and `usde_withdrawal_value` columns relevant to
  our pricing (i.e. do they affect what we'd report as the venue's USD
  value at a given block)?

#### S2. Cat A par-stable holdings — any other off-chain yield sources?
After PR 1 (2026-05-05), the only Cat A venue earning revenue is **S26
USDC raw at ALM**, via the Anchorage escrow registered in
`external_alm_sources` (~$891K/mo from interest sweeps). The other Cat A
venues — **PYUSD** (S28), **USDT** (S27), **DAI** (S29), **USDe** (S30),
**USDS raw** (S31) — still generate **$0** `prime_agent_revenue`
because no off-chain yield source is registered for them.

Confirm Spark isn't earning yield from any off-chain custodian on those
other par-stable holdings (i.e., is the current `external_alm_sources`
allowlist for Spark — just the Anchorage escrow — complete, or are
there other addresses we should add?).

#### S14. sparkPrimeUSDC1 (S18) — Arkis API NAV vs on-chain `convertToAssets()`
Persistent ~0.7% drift vs Spark's view at all 3 EoM dates. Spark's
amounts are suspiciously round ($15.00M / $10.10M / $10.10M), suggesting
the API NAV is rounded. Is the on-chain `convertToAssets()` authoritative
for revenue recognition, or should we consume Arkis's API NAV (which
Spark uses)? Bias on our prime_agent_revenue: +$60–100K/quarter
over-statement.

### P2 — sanity checks / confirmations

#### S5. Subsidy reference rate — confirm EFFR
Configured with `ref_rate_kind: effr` per your guidance. Confirm this is
the long-term spec.

#### S10. L2 sUSDS proxies (S37 Base, S43 Arbitrum, S47 Optimism, S51 Unichain) — Q1 flow confirmation
Each has only one row in our captured fixture (pre-period anchor only).
The Q1 filter drops some entirely; the math happens to work because no
Q1 mid-month flows occurred, but this is unverified. Confirm no L2 sUSDS
deposits/withdrawals happened in Q1 2026.

#### S15. PYUSDUSDS Curve pool (S25) — confirm not Sky Direct
S25 PYUSDUSDS isn't flagged Sky Direct (per the Atlas spec — Sky Direct
on Curve only covers sUSDS/USDT for Spark). Worth confirming.

#### S16. Q1 2026 Spark prime_agent_revenue ≈ $5.5M/month
Computed from: ~$1.5B utilized × ~4% blended APY. Sources: SparkLend
spTokens, sUSDS, syrup. Does this match Spark's internal accounting?

#### S17. PSM3 holdings grew $378M → $509M during Q1
Major contributor to the utilized reduction. Is this growth from new Sky
borrowing (capital deployment) or from PSM3 yield accrual?

### P3 — future-proofing / operational / dormant

#### S8. Per-protocol "idle vs allocated" split (~ANSWERED via `result_spark_*_by_alm_proxy` tables)

Every per-protocol table publishes:
```
alm_supply_amount, alm_share, alm_idle (= alm_share × protocol_idle_amount)
```
The `alm_idle` is **economically meaningful**: it's the portion of
Spark's lending-pool supply that hasn't been borrowed by counterparties
yet (the protocol couldn't lend it out). It still earns the supply rate
but is at risk of de-allocation, so Spark accounts for it separately.

Resolution: extend `Venue` to expose two values when the venue is a
shared lending pool: `alm_supply_amount` and `alm_idle`. Total venue
value = supply; allocated = supply − idle. **No question to Spark; this
is now an internal todo for the settle pipeline.**

#### S9. ETH-denominated balance sheet for spETH
The dashboard publishes spETH's balance sheet in **ETH units** (with
`eth_price` only used for headline USD conversion). Our snapshot is
USD-only. For spETH (and any future non-USD-denominated vault), should
the prime-level snapshot maintain a per-asset-class native-unit subsheet?
Lower priority — spETH is small ($185M) and snapping ETH→USD at one
block introduces sub-bp drift only.

#### S11. Captured fixtures are partial
`tests/fixtures/spark_2026_q1/cat_b_cum_balance.json` was hand-compacted
from larger Dune outputs; some venues lost daily granularity (e.g., S34
was rebuilt after losing 12 days of January flows). Future re-runs may
diverge from a fresh Dune query. Could Spark persist the raw Dune
executions as JSON dumps somewhere we can re-pull from?

#### S12. fsUSDS pricing approximation
S17/S36/S42 (Fluid Savings USDS variants) declare sUSDS as the
underlying. Compute prices as `convertToAssets(fsUSDS) × $1` (sUSDS
treated as par). Reality: sUSDS pps was ~$1.07 at Q1 EoM, so this
**understates value by ~7%**. Doesn't affect Q1 (all three venues hold
$0) but will if any are reactivated.

#### S13. USTB (S21) and USCC (S22) — no NAV oracle
Both default to `nav_oracle: const_one`. Both held $0 by Q1 2026 so the
simplification has no impact. Superstate publishes NAV via API — needed
before re-activation.

#### S18. Make the SLL Assets/Liabilities dashboard queries public
[The dashboard](https://dune.com/sparkdotfi/spark-sll-nav-to-liabilities)
is publicly viewable but **12 of 19 underlying queries are private** —
we can read the column shape via public visualization metadata, but not
the SQL. Could the Spark team make the following query IDs public so we
can validate methodology end-to-end? (Identifying by visualization ID +
position since query IDs aren't exposed for private viz.)

| visualizationId | dashboard position (col, row) | likely metric |
|---|---|---|
| 9369726 | (0, 3) — top-line counter | total assets |
| 9369744 | (2, 3) — top-line counter | total liabilities |
| 9369748 | (4, 3) — top-line counter | net surplus |
| 9370106 | (0, 7) — secondary counter | total allocated |
| 9369761 | (2, 7) — secondary counter | total idle |
| 9369762 | (4, 7) — secondary counter | deployment efficiency |
| 9878455 | (0, 11) — chart | (allocated/idle trend?) |
| 9878457 | (3, 11) — chart | — |
| 9335139 | (0, 15) — wide chart | — |
| 9335051 | (0, 23) — wide chart | — |
| 9367753 | (0, 32) — wide chart | — |
| 9343831 | (3, 40) — drilldown | — |

Already-public queries (no action needed): `5747940`, `5776184`,
`6866703`.

---

## BA labs

### P0 — material numerical gaps

#### B4. `assets` derivation — protocol-level metric or position-sum?
Your `/stars/grove/ assets` = $3.19B is bigger than the on-chain
position-sum we can verify (~$2.86B; gap ~$325M). Is this derived from
Sky's `Vat.urns(ilk).ink` field (collateral pre-deposited against the
ilk), or computed at the Sky-protocol level via a different metric? We'd
like to reproduce the same number from on-chain primitives.

### P1 — methodology unknowns affecting accuracy

#### B2. Spark `treasury_balance` ($37M) — likely Eth ALM USDS balance
No `result_spark_*_treasury_*` table exists. The most plausible source
is the Eth ALM proxy's USDS balance (`balanceOf(USDS, 0x1601…347e)` at
the time). For Grove this matches exactly ($22.8M). For Spark our
snapshot reads $0 from the subproxy but we don't currently read the
ALM's USDS holding (because that's already counted under venue S31
"USDS raw / POL ALM idle"). **Q for BA:** is `treasury_balance` =
ALM-side USDS for Spark? Or a different address?

#### B3. Spark `liabilities = debt + sUSDS_POL` — intentional accounting?
Your `/stars/spark/ liabilities` ($6.77B) = our debt ($4.30B) +
sUSDS_POL ($2.47B). This implies you treat Spark's sUSDS holdings as a
liability (deposits owed at savings rate). Spark's own dashboard's spETH
section confirms this two-source liability model
(`liabilities_sky` + `liabilities_savings_v2`). Is your `liabilities`
field intentionally `debt + sUSDS_POL` for Spark, and would you extend
it to `debt + Σ savings_v2_liabilities` once spX vaults grow?

#### B5. STAC (E7) NAV — which oracle is canonical?
Our snapshot reads STAC at $1.0157 via Chronicle (`0x9d77…58b`,
reflecting real CLO yield accrual). Your `/allocations/?star=grove`
reports STAC at $1.00 flat. Per `docs/pricing/allocation_pricing.csv`
STAC has Chronicle as Oracle1 and Redstone (`0xedc6…d7d`) as Oracle2.
Are you using Redstone, const_one, or a different feed? If const_one is
canonical, we should switch our NAV path to match (currently whitelisted
as "known divergence" — drift ~1.5%).

#### B10. Why does Sky + Prime ≠ Total for USDe / Superstate / BUIDL?
Raised in BA call #1 (see PRD §17.13). For these three venues the
settlement we observed had `Sky Revenue + Prime Revenue ≠ Total Revenue`.
BA's explanation: manual calculations using formulas from a private deal
(USDe, no on-chain interest), or manual NAV updates (Superstate Crypto
Carry Fund, BUIDL).

For MSC: should we replicate the manual math in code (i.e. add a
`MANUAL_OFFCHAIN` pricing category fed from a YAML / Dune feed of
operator-supplied values), or treat these venues as audit exceptions
that MSC reports separately and reconciles against BA's published
output? The choice affects whether MSC can fully close `monthly_pnl`
without external inputs.

#### B13. SDE — current list, version, AND settlement semantics
Raised in BA call #1 (see PRD §17.13). Two related asks bundled
together because they need the same conversation with Sky/BA:

**(a) Canonical list / Atlas version.** Our
`config/sky_direct_exposures.yaml` is hand-maintained against the Sky
Atlas spec; the latest snapshot recorded there is from 2026-04-29.
Could BA share:
- the canonical list of SDEs as of today,
- the Atlas commit / version that list was derived from, and
- any expected near-term changes (new entries, terminations, cap
  adjustments)?

We need this before re-running settlements for any month after April
2026 to avoid silent drift between our SDE table and Sky's intent.

**(b) Settlement semantics — Sky-takes-all vs. prime-keeps-surplus.**
There's a documented discrepancy between two sources:

- The Nov-2025 Atlas edit changed SDE settlement so the Prime Agent
  no longer retains the surplus revenue over BR — instead Sky receives
  **all** SDE revenue. Source: [Atlas Edit, Weekly Cycle Proposal
  Week of 2025-11-17](https://forum.skyeco.com/t/atlas-edit-weekly-cycle-proposal-week-of-2025-11-17/27421).
- The current `prime-settlement-methodology.md` in `laniakea-docs`
  (and any code derived from it) qualitatively says "Sky receives
  all revenue" in the Step 4 Rules, but the worked formula leaves the
  prime with the surplus over BR. Source:
  [`sky-ecosystem/laniakea-docs/blob/main/accounting/prime-settlement-methodology.md`](https://github.com/sky-ecosystem/laniakea-docs/blob/main/accounting/prime-settlement-methodology.md).

Our current MSC working methodology attributes **all SDE revenue to
Sky** (matching the Atlas edit + the qualitative Rules text). Two
things to confirm:

1. Is the Atlas edit definitive — i.e. Prime Agents are to receive
   **no** revenue from SDEs going forward?
2. Are the prime-agent teams aware the methodology change was made?
   (Their internal accounting may still match the laniakea-docs
   formula, in which case our MSC numbers will diverge from their
   workbooks on every SDE venue.)

If Sky-takes-all is confirmed, we'll keep our current MSC behaviour
and document the laniakea-docs formula gap as known divergence
pending a docs update.

### P2 — sanity checks / confirmations

#### B1. Spark `idle_assets` ($720M) — informational; reconstructable from public tables

**Status: informational only.** This question doesn't affect our
settlement methodology — we don't currently consume BA's `idle_assets`
in any computation. It's a reconciliation cross-check.

Two-layer reconstruction (verified 2026-05-04):

**Layer 1 — protocol-supply idle.** Every per-protocol table
(`result_spark_idle_*_by_alm_proxy`, `result_spark_aave_*_by_alm_proxy`,
`result_spark_maple_*_by_alm_proxy`, `result_spark_curve_pool_apr`)
publishes `alm_idle = alm_share × protocol_idle_amount` — Spark's
portion of un-borrowed supply. Sum across all such tables ≈ **$465M**
visible across 13 venues.

**Layer 2 — PSM3 / ALM-raw / Foundation.** The table
`dune.sparkdotfi.result_spark_usds_s_usds_usdc_in_psm_3_curve_psm_3_proxy_foundation_aave`
publishes raw USDS / sUSDS / USDC balances at non-protocol addresses,
keyed by `(blockchain, protocol_name ∈ {PSM3, ALM Proxy, Curve PSM3
Proxy, Foundation, Aave}, token_symbol, amount)`. Latest-dt totals:

| protocol_name | total |
|---|---|
| ALM Proxy (Eth + L2s) | ~$4.32B (incl. Eth ALM sUSDS $2.25B = our S32, USDT $746M = S27, PYUSD $678M = S28) |
| PSM3 (Base/Arb/Op/Uni) | ~$520M |
| Foundation (Eth) | $1.1M |

Layer-1 + Layer-2 raw sum ≈ **$5.3B** — much larger than your
`idle_assets` ($720M), so BA must split this across multiple aggregate
fields:
- `liabilities` ($6.77B) absorbs sUSDS POL ($2.47B = ETH ALM sUSDS + L2 ALM sUSDS) per question B3
- `/allocations` per-venue absorbs USDT/PYUSD/USDC raw at ALM
- `in_transit_assets` may absorb L2 ALM USDS bridges
- the residual that *should* be `idle_assets` is the leftover slice

**Refined Q for BA (audit, not blocker):** which `(protocol_name,
token_symbol, blockchain)` rows from `result_*_psm_3_proxy_foundation_aave`
are summed into `idle_assets`, vs. which get folded into `liabilities`,
`in_transit_assets`, or per-venue `/allocations`? With that mapping plus
Layer 1's `Σ alm_idle`, we can reproduce `idle_assets` from on-chain
primitives (we already read every address in this table — per-chain
PSM3 reads via `compute._psm`, ALM-side raw balances via the venue
inventory and `_read_idle_holdings`).

#### B7. List of historical offchain transfers + revenue exceptions
Raised in BA call #1 (see PRD §17.13). BA confirmed that offchain
transfers (Anchorage interest sweeps, Merkl rewards, BUIDL yield mints,
AVAX-side rewards, etc.) reach the ALM Proxy as ordinary token
transfers and are picked up by `external_alm_sources` accounting on the
pipeline side.

What we still need: the **enumerated list** of all such transfers /
exception streams to date — Miha mentioned BA can pull this by walking
ALM Proxy inflows. Useful for:
- validating our `external_alm_sources` allowlist is complete
  (Spark currently only registers the Anchorage escrow),
- spotting any stream we're silently missing,
- back-filling historical settlements where applicable.

Operational, not methodology-blocking.

#### B8. Enumeration of "edge case" venues — exhaustive?
Raised in BA call #1 (see PRD §17.13). BA flagged USDe, Superstate
Crypto Carry Fund, and BUIDL as "edge cases" where settlement uses
manual calculations. Confirm the enumeration is exhaustive — i.e. no
other Spark / Grove / OBEX venue has the same kind of offchain-formula
or manual-NAV treatment that we should be aware of? If others exist,
list them so we can flag them in our YAML configs and avoid silent
mis-pricing.

#### B9. Aave / SparkLend principal-vs-accrued — improve via events or accept differences?
Raised in BA call #1 (see PRD §17.13). BA's current method for
separating principal from accrued interest is:
`accrued_over_day × (1 − utilization) × BR`. Our pipeline uses the
closed-form `scaledBalanceOf × liquidityIndex` route (Cat C, see PRD
§17.4) which doesn't reuse BA's formula but is exact for Aave V3 +
SparkLend (each `scaled_balance_at × index` is the principal-bearing
view).

Worth confirming: is the small difference between our value and BA's
expected (BA's quote: "should we improve, or live with small
differences?") explained by the formula gap, by indexing precision, or
by something else? And which is the canonical reference for
reconciliation?

#### B6. `/allocations/?star={prime}` is incomplete — by design or omission?
Several of our venues are missing from your `/allocations` endpoint
even though their value rolls into your `/stars/{prime}/ assets`:
- Grove: E9 JTRSY ($1.17B), E10 BUIDL ($984M), E12 V3 NFT ($25M),
  E6 grove-bbqAUSD-V2 ($25M)
- Spark: S1 spUSDS ($156M), S3 spUSDT ($616M), S4 spDAI ($257M),
  S5 spPYUSD ($100M), S14 syrupUSDC ($105M), S27 USDT raw ($750M),
  S28 PYUSD raw ($677M), S32 sUSDS POL ($2.47B), L2 USDS POL on
  Base/Arb/Op/Uni (~$90M each)

Is the `/allocations` endpoint intended to be a complete catalog, or
deliberately filtered (e.g., excluding Sky Direct positions, raw idle,
sUSDS POL)? If filtered, what's the rule?


---

## Resolved

Resolved questions move here from their open section when their GitHub
issue is closed. The full resolution narrative lives in
`PRD.md §17.13` (review-acks); this section keeps a compact pointer
trail (Q-ID, title, close date, issue link).

### S3. Anchorage S23 — $150M tri-party loan, on-chain addresses confirmed
**Resolved 2026-05-05** via [#17](https://github.com/soterlabs/settlement-cycle/issues/17). See `PRD.md §17.13`.

