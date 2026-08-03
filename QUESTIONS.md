# MSC — Open Questions

Single source of truth for outstanding questions across the MSC settlement
pipeline. Grouped by recipient (**Grove team**, **Spark team**, **BA labs**)
and ordered by priority within each group:

- **P0** — material numerical gap in the current settlement output
- **P1** — methodology unknown that would shift numbers if confirmed/changed
- **P2** — sanity check / confirmation, no current numerical impact
- **P3** — future-proofing, operational, or dormant (venue holds $0)

Question IDs (G1, S6, B4, …) are stable and cross-referenced from `PRD.md §17`.
Last consolidated: 2026-05-06. Pre-Q-ID resolutions (subsidised rate,
PSM3 daily sampling, hardcoded EoM blocks, Foundation USDS, GACLO-1
valuation, ~$1.13M Sky-Share residual) are tracked under `## Resolved`
below as compact pointers; the full narrative lives in `PRD.md §17`.

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
revenue. Until Grove identifies the canonical feed, E1 revenue is
under-counted by ~$430K/month going forward; historical back-fill is
unlikely to be reliable (see BA-call-#2 note below).

**Update from BA call #2** (see PRD §17.13): BA flagged that historical
Merkl reconstruction is "not possible" in their experience — payment
token varies (sometimes paid in volatile tokens), reporting is
inconsistent. BA's preferred approach is to capture rewards only once
they hit the ALM as a stable token (same boundary rule as volatile
tokens generally). This doesn't resolve G3 — Grove still needs to
identify which feed populates their `Rewards` column going forward —
but it sets expectations that any historical back-fill from Merkl
itself will be approximate at best, so the resolution will likely be
forward-only via ALM ingress capture rather than retroactive
correction of pre-2026-05 settlements.

**Update 2026-05-13 — partially resolved via Option A (Cat C external-rewards path).**
Dune verification (query 7489308) confirmed Merkl IS the source: two
claim events delivered aTokens (NOT the underlying RLUSD) to the Grove
ALM on Ethereum — Feb 6 2026 (`aEthRLUSD` ≈$2.96M + `aHorRwaRLUSD`
≈$821K, tx `0x8a81d6dd…704a`) and Apr 24 2026 (`aEthRLUSD` ≈$1.41M +
`aHorRwaRLUSD` ≈$979K, tx `0xd374d598…e3e7`). Both were initiated via
a Grove Gnosis Safe (`0x0eec…f85f`) calling Merkl's `claim()`. The
Merkl distributor is `0x3Ef3D8bA38EBe18DB133cEc108f4D14CE00Dd9Ae` —
verified on-chain as the canonical Angle Labs / Merkl
DistributionCreator proxy (EIP-1967 transparent proxy, implementation
at `0x33cc998fd4af3b6be42bac9a67fe97e9e275d2ae`).

What this implementation does: a new `_atoken_external_revenue_usd`
helper dispatches per sender. For Merkl distributors (currently just
`0x3Ef3D8bA…D9Ae` on Ethereum, tracked in `_MERKL_DISTRIBUTORS`) it
reads the on-chain `Claimed(user, token, amount)` event the distributor
emits — one canonical event per (user, token) per claim, independent of
Merkl's post-claim Aave/wrapper routing. For other senders it falls
through to a generic per-sender `Transfer(from=…, to=ALM)` reader
suitable for direct sweeps (Anchorage interest, BUIDL yield mints). The
result × 10**-decimals (par-stable underlying assumed; raises otherwise)
is routed to a new `VenueRevenue.external_revenue` field that flows
100 % to prime (not subject to SDE-splitting). Grove's
`external_alm_sources.ethereum` lists the Merkl proxy. SQL files:
`queries/merkl_claims_ethereum.sql` (Claimed events) and
`queries/atoken_external_inflow.sql` (generic Transfer events) — both
cached via the existing `@cached(source_id="dune.execute")` decorator.

**Why Claimed instead of Transfer.** The first iteration used the
generic Transfer-based path for all senders. Inspecting the actual
claim tx (`0x8a81d6dd…704a`) showed Merkl's Aave flow has THREE
addresses per claim per token: the Aave pool proxy (which mints + sends
the real aToken), the Merkl distributor (which moves a separate static-
aToken wrapper), and the `0x0` mint event. The configured Merkl address
matched none of the transfers of the actual venue aTokens — the
pool-proxy address was the right `from`, but pool addresses can't be
allowlisted without classifying ordinary deposits as revenue. The
Claimed event sidesteps all of this and stays robust to Merkl rotating
intermediary contracts.

**Why the SQL JOINs to the aToken `Mint` event (and not just filters on
`Claimed.token`).** Merkl's `Claimed(user, token, amount)` event records
`token` as the *Merkl reward token* — Aave's staticAToken / LM wrapper
(e.g. `0x72eeed80…` for aEthRLUSD, `0x503d751b…` for aHorRwaRLUSD),
NOT the underlying aToken the ALM ends up holding. Filtering on
`venue.token.address` against `Claimed.topic2` returns zero rows
(discovered during the 2026-05-14 verification). The Aave V3 aToken
contract emits `Mint(caller, onBehalfOf, value, …)` alongside the
staticAToken's redeem inside the same tx, with `caller = staticAToken`
(= `Claimed.token`) and `onBehalfOf = ALM` (= `Claimed.user`). So
`queries/merkl_claims_ethereum.sql` pairs the two via
`(c.tx_hash, c.topic2) == (m.tx_hash, m.topic1) AND m.contract_address
= {{atoken}}` and uses `Claimed.amount` as the canonical value. When a
single tx claims rewards for multiple aTokens (the Feb 6 tx claims for
BOTH aHorRwaRLUSD and aEthRLUSD), each Claimed pairs with exactly one
Mint — no double-counting. The operator-facing surface stays clean:
`grove.yaml` only specifies the Merkl distributor address (in
`external_alm_sources`) and the venue aToken address (in `venue.token`).
Merkl-internal addresses (staticAToken wrappers) are derived per-tx via
the JOIN.

**Live verification (2026-05-14).** End-to-end call to
`_merkl_claims_revenue_usd` against Dune through the real
`load_prime("config/grove.yaml")` + `Period` objects returned:
Feb 2026 E1=$821,306.03 / E3=$2,963,561.64 (subtotal $3,784,867.67) and
Apr 2026 E1=$978,913.67 / E3=$1,411,897.31 (subtotal $2,390,810.98) —
grand total $6,175,678.65. Matches the expected per-claim amounts from
the original Dune verification (query 7489308) to the cent.

What remains open:
- **Accrued-but-unclaimed**: Grove's PnL workbook credits monthly
  accrual (Rewards column grows daily; `claimed` only updates when a
  Safe tx fires Merkl's claim). Our approach matches BA's
  ALM-ingress-boundary preference, so we differ from Grove on the
  *timing* of revenue attribution: Grove sees ~$447K/month accrued on
  E1; we see $821K landing in Feb (the claim of Jan + early-Feb
  accrual) and $979K landing in Apr. The lifetime totals match;
  per-month numbers will differ until Grove either claims monthly or
  switches their workbook to ingress-boundary accounting.
- **Non-aToken Merkl drops**: if a future Merkl campaign distributes
  the underlying token (RLUSD) instead of the aToken, the Cat A path
  (`_cat_a_capital_inflow_timeseries`) already handles that via the
  same `external_alm_sources` allowlist (the two categories are
  orthogonal — Cat A filters its venue's stable-token transfers;
  Cat C filters its venue's aToken transfers).
- **Other primes**: Spark / Obex don't have any documented Merkl
  campaigns today. If they appear, add an entry to that prime's
  `external_alm_sources` and the same path applies.

#### G19. Agora — 8% on deployed AUSD, split between native yield and an undefined component
Raised in Grove team interview (2026-05-06). Grove described an
ongoing partnership with Agora paying **8% on amount deployed**,
split between native yield (presumably the AUSD-position yield) and
a secondary component that wasn't specified. Likely affects E11
(Curve AUSD/USDC LP) and/or E12 (Uni V3 NFT with AUSD), but the
reward attribution rule (per-position vs pooled at ALM) was left
open in the interview.

**Q for Grove:**
1. What is the secondary component of the 8% split — issuer rebate,
   referral payment, distribution-reward bonus, something else?
2. Is the 8% an APY on deployed principal, or a one-off rebate per
   subscription period?
3. Does it land in the ALM Proxy as a stable token (USDC / AUSD /
   USDS) — in which case the existing `external_alm_sources`
   plumbing captures it under Cat A — or per-venue? Per BA call #2
   Q5, MSC won't try to apportion per-position even if the source is
   technically traceable, so the right place to capture the 8% is
   at ALM ingress.

If the second component is being paid out today and we're not
capturing it, Grove `prime_agent_revenue` is being under-counted by
the applicable share of 8% × deployed AUSD — material until Grove
identifies the secondary component and either confirms it's already
flowing through ALM ingress or names a new source we need to plumb.

### P1 — methodology unknowns affecting accuracy


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

#### G20. FundingMorpho contract — DR-eligible per Grove, but feed mechanism unspecified
Raised in Grove team interview (2026-05-06). Grove confirmed a
**FundingMorpho** contract is now part of their distribution-rewards
sources, alongside the legacy ref-code mechanism. Today MSC reports
`MonthlyPnL.distribution_rewards = $0` for Grove (Phase-3 placeholder
per PRD §17.6, populated only when a real source lands —
historically only for Skybase). Lighting up FundingMorpho requires:

**Q for Grove:**
1. **Contract address** of the FundingMorpho instance.
2. **Read semantics** — are rewards (a) accrued via a `claimable()`
   view function, (b) emitted as `Transfer` / custom events, (c)
   periodically swept on-chain to the ALM Proxy as a stable token,
   or (d) tracked off-chain and posted to a dashboard?
3. **Eligibility window** — was Grove eligible from a specific date
   (e.g. 2026-01 MSC start), or does it start later, or is it
   retroactive?

If (c) — sweeps to ALM Proxy as a stable token — the existing
`external_alm_sources` plumbing handles it natively and the reward
flows through Cat A `prime_agent_revenue`, no new
`distribution_rewards` reader needed. If (a) or (b), MSC needs a
new `IDistributionRewardsSource` adapter.

#### G21. Galaxy Arch CLO (GACLO-1, E21) — confirm USDC distribution payer + cadence
Raised in PR #67 review (2026-05-11). The PR un-skips E20 (JAAA-avax,
Chronicle on Eth verified) but keeps E21 (GACLO-1) `skip: true`, with
the design that the yield is captured via monthly USDC sweeps from
Galaxy to the Grove ALM, recognized as Cat A revenue via
`external_alm_sources`.

**On-chain finding (Dune verified 2026-05-11):** no USDC sweep from
Galaxy has been observed to date. Trace:

1. The GACLO-1 issuer is **`0x5ee36f573f0e543f905796c0e697caa7e984e0c8`**.
   It minted GACLO-1 to three holders on a single day (2025-12-16):
   - Grove ALM Avalanche `0x7107dd8f…3644` — 49.9M GACLO-1
   - `0x05855717…ae3b` — 100M GACLO-1
   - `0xe58e386d…56f4` — 0.1M GACLO-1
2. Since 2025-12-16, the issuer has sent **zero USDC** to any address
   on any chain. Lifetime outbound from this address is GACLO-1 mints
   + airdrop-style spam tokens; no stablecoin distributions.
3. Grove ALM Avalanche has received only three token kinds since
   inception: JAAA (subscriptions Aug-Sep 2025), AVAX (gas
   replenishment), and GACLO-1 (the single 2025-12-16 subscription).
   **No USDC received, ever.**

So as of 2026-05-11, ~5 months after subscription, no monthly Galaxy
distribution mechanism is empirically visible on-chain.

**Q for Grove:**
1. **Has Galaxy made any distribution since 2025-12-16?** If so, on
   what chain, in what token, to what Grove address?
2. **Confirm the payer address** if distributions are still pending —
   is it the issuer (`0x5ee36f57…0c8`), a separate treasury / paying
   agent multisig, or paid out-of-band?
3. **Cadence** — monthly on the 10th as the PR comment assumes, or
   different (quarterly, annual coupon)?
4. **Settlement asset** — USDC on Avalanche, USDC on Ethereum, or AVAX?

Until distributions land, E21 contributes $0 to revenue (correct
under the current `skip: true` + empty `external_alm_sources` config).
Numerical impact when distributions begin: depends on coupon size.
Galaxy publishes CLO trances with ~3-7% APY on the underlying — so a
$49.9M position could pay $1.5-3.5M/yr split into ~12 monthly
sweeps if the cadence assumption holds.

#### G23. CoF on Net_Subs — BR computation + vat.grab inclusion in Subscriptions
Raised 2026-06-04 after a clean SDE-share methodology bisect (PR-this-branch).
With daily-resolved `sd_share` + burn-day override, our per-venue `sd_revenue`
matches Grove's workbook to within upstream `actual_revenue` drift on all
four months of Jan-Apr 2026 (Σ Δ sd_rev = −$36K, dominated by Mar
Centrifuge `Deposit/Withdraw.assets` events vs `Transfer × NAV`
accounting). After that fix, the residual headline gap vs Grove
decomposes as:

| Month | Δ Σ sd_revenue | **Δ cof_total** | Δ sky_revenue |
|-------|---:|---:|---:|
| Jan | −$3,311 | **+$60,078** | +$56,767 |
| Feb | −$5,827 | **+$100,790** | +$94,963 |
| Mar | −$26,354 | −$42,060 | −$68,414 |
| Apr | −$649 | **+$124,933** | +$124,284 |
| **Σ** | **−$36,141** | **+$243,741** | **+$207,600** |

So **+$244K Σ Jan-Apr is in `cof_total` (BR × Net_Subs)** — the SDE-split
side is solved. Two known methodology differences likely explain the gap
(both on the Grove side, not in our pipeline):

1. **BR rate computation.** We compute the daily subsidised BR per Sky
   governance (`subsidised_apy_d = ref_rate_d + (BR_d − ref_rate_d) × T / 24`,
   T = months since 2026-01-01, capped at 24). Grove's workbook may
   use a different rate (constant per-month BR, average BR, or a
   stale formula). The over-attribution rate vs Grove is ~0.046% of
   avg Net_Subs — too small for a flagrant rate error, but consistent
   with e.g. a subsidy-ramp truncation or a slightly different
   per-day BR sampling.
2. **`vat.grab` inclusion in Net_Subs.** Per PR #103 (2026-06-03) we
   now include `vat.grab` events alongside `vat.frob` in `cum_debt`
   so that our Net_Subs matches `Vat.urns(ALLOCATOR-BLOOM-A).Art`
   exactly. **Grove's "Subscriptions" column appears to only read
   `vat.frob` events**, missing the monthly Sky-Share spell that
   capitalizes accrued Sky revenue via `vat.grab` (see PRD §13 / the
   vat.grab transaction list shared with Grove team 2026-06-03).
   Cumulative grab through 2026-05-11 is **$57.91M** — so any
   downstream BR calculation that uses Grove's frob-only
   "Subscriptions" series will under-report Net_Subs by that amount,
   under-charging CoF.

**Q for Grove:**
1. **BR rate** — what formula does the workbook's CoF column use? Is it
   a daily subsidised rate per the Sky-governance formula, a constant
   per-month BR, or something else?
2. **Subscriptions column composition** — does it sum only `vat.frob`
   events on `ALLOCATOR-BLOOM-A`, or does it also include `vat.grab`
   events from the monthly Sky-Share spell? If the latter, what
   selector / event filter do you use?
3. **Reconciliation** — both sides should reconcile to
   `Vat.urns(ALLOCATOR-BLOOM-A).Art × Vat.ilks(ALLOCATOR-BLOOM-A).rate`.
   Can your team confirm or share your Net_Subs derivation so we can
   bisect the +$244K?

Numerical impact: pinning this resolves the residual +$207K Σ Jan-Apr
headline gap (down to ~$36K of pure upstream `actual_revenue` drift).
Cross-ref **PRD §17.13 item 8**.

### P2 — sanity checks / confirmations

#### G25. spUSDG on Robinhood — how is the Spark–Grove two-Star revenue split settled?
The spUSDG deployment on Robinhood Chain (forum t/28031) routes Spark
Savings USDG deposits through Spark's ALM proxy (`0xfD2fD4B0…dB24`) into
the Grove USDG Morpho Vault (`0xBEEff039…54d9`, Steakhouse-curated). The
technical-scope post specifies the contract architecture but not the
financial terms. As of 2026-08-03 the Morpho vault held $1.97 and
Spark's ALM held zero shares (all ~$23.8M of depositor USDG still sits
in the spUSDG vault), so there is no numerical impact yet — but once the
deployment leg goes live we need to know:

1. How is the yield split between Spark (savings product, VSR payer) and
   Grove (vault curation)? Fixed curator/performance fee to Grove, or a
   negotiated share?
2. Should the Morpho-vault position appear on Spark's books (its ALM
   holds the shares — our current plan), on Grove's, or both with an
   offsetting liability?
3. Does the deposited USDG count as MSC-perimeter capital for either
   prime, given it is depositor-funded (like the other Savings V2
   vaults, which we track position-only outside the perimeter)?

Current treatment: Spark venue **S63** tracks the spUSDG vault
position-only (config/spark.yaml); the Grove-side venue is a commented
stub (**E39**, config/grove.yaml) pending this answer.

#### G4. Sky Direct venue set re-confirmation
Per the Atlas spec: Treasury Bills on Eth (BUIDL/JTRSY/USTB) +
USDC in PSM3 non-Eth + USDT in sUSDS/USDT Curve. Grove's currently
flagged Sky Direct: **E9 JTRSY** + **E10 BUIDL** (driven by
`config/sky_direct_exposures.yaml`, the time-bounded SDE table; the
older per-venue `sky_direct: true` flag in `<prime>.yaml` is deprecated
and ignored by compute). Confirm no other Grove venue should be Sky
Direct as of today, and please flag this list for re-review whenever
the Atlas Sky Direct section changes.

#### G5. Subsidy ramp — '3-month windows' note (quarterly settlement vs. daily compound?)
We've configured Grove with `ref_rate_kind: tbill_3m` per your guidance,
matching what the Feb 2026 PnL workbook empirically used (3.67–3.74%
range, vs EFFR's ~4.33%). Confirm this is the long-term spec, not just a
Feb-2026 implementation choice. Same question for Spark (currently using
EFFR per same guidance).

**Update from Grove interview (2026-05-06):** Grove reconfirmed **3M
T-Bill** + 2026-01-01 anchor for the subsidy ramp — resolves the
tenor sub-question for Grove. One ambiguous side note from the
interview: "send value calculations for 3-month windows." Possible
readings: (a) Grove computes the subsidy in 3-month buckets and
settles quarterly rather than the daily compound MSC currently uses,
or (b) it's just a reference to the 3M T-Bill rate tenor and not a
statement about settlement frequency. Worth a one-line confirmation
from Grove on which they meant. Cross-ref **B15** (T-Bill sampling
frequency, still open with BA / Sky).

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

#### G22. E9 JTRSY NAV — BA Labs indexer prices ~2% lower than Centrifuge pricePerShareFeed
Raised by the BA-parity integration test (`tests/integration/test_ba_parity.py::test_grove_parity`, 2026-05-12 run). MSC prices E9 at ~$1.099B EoM (Centrifuge `pricePerShareFeed` at `0xFE6920eB6C421f1179cA8c8d4170530CDBdfd77A`); BA Labs' indexer reports ~$1.122B, a ~$22.6M / 2.02% gap. The MSC value matches the Grove team's own PnL workbook actual_revenue within ~$100/month (vs Chronicle's ~$146K/mo divergence) — see PRD §17.7's 2026-05-02 oracle-switch entry; BA Labs hasn't migrated to `pricePerShareFeed`.

Same shape as E7 STAC where MSC uses Chronicle (~$1.017 NAV) vs BA's const $1.00 — both whitelisted in `KNOWN_NAV_DIVERGENCES` so the integration test reports "(known NAV oracle divergence)" rather than failing. Real NAV growth, not a methodology bug.

**Q for BA Labs:** Migrate the JTRSY indexer to read Centrifuge's `pricePerShareFeed` (or surface the underlying NAV from the Pool Manager contract) so the stars-api `assets` field tracks actual NAV growth. Until then MSC and BA disagree by 1–2% on JTRSY EoM positions, with MSC matching Grove's workbook.

#### G8. Centrifuge tranche tokens — backup NAV feed
Switched 2026-05-02 from Chronicle to Centrifuge `pricePerShareFeed`
(`0x4880…0B` for E8 JAAA, `0xFE69…77A` for E9 JTRSY) per Grove team's PnL
workbook. Does Centrifuge expose another backup (Pool Manager contract
or off-chain API) we could fall through to if `pricePerShareFeed` goes
down? ACRDX dropped −0.69% in March 2026 — silently falling through to
const_one would mask a real loss month.

#### G18. E8 JAAA / E9 JTRSY — Jan 1 dates don't match Atlas
Raised in Grove team interview (2026-05-06). Grove flagged that the
Jan 1 dates associated with JAAA / JTRSY (E8 / E9) don't match what
Atlas records. Rune confirmed Atlas is the authoritative source, so
the mismatch needs reconciliation in Grove's direction.

What's unclear from the interview note: which "Jan 1 date" is
mismatched — pricing inception (when the NAV oracle starts publishing
meaningful values), Sky Direct classification start (when the venue
became flagged SDE in `config/sky_direct_exposures.yaml`), or the
debt-accounting boundary?

**Q for Grove:**
1. Which date is mismatched, and by how much (JAAA earlier than
   Atlas, or later)?
2. Does the mismatch affect the SDE-flag window for E8/E9? If so,
   `config/sky_direct_exposures.yaml` time-bounds may need an update
   — material because Sky-takes-all on SDE revenue, so a wider
   window shifts more revenue from prime to Sky.
3. Same question for any other Centrifuge-priced venue (E22 ACRDX
   uses Centrifuge `pricePerShareFeed` too).

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

#### G17. Historical "lesser of (debt to Sky, NAV)" payment pattern — pre-MSC settlements
Raised in Grove team interview (2026-05-06). Grove indicated that in
some pre-MSC months they paid Sky the **lesser of (debt owed to
Sky, current NAV)**, and in some months they didn't — i.e. the cap
on monthly payment was inconsistent. Specific concern raised in the
interview: months where the prime ran a **negative PnL** would
otherwise force a transfer above NAV without this cap.

**Status: informational only.** This question doesn't affect MSC's
forward methodology — the MSC monthly cycle starts 2026-01 and uses
a different payment formula (`compute/sky_revenue.py`: full
`sky_revenue` per the SDE-split model, no NAV cap). It's only
relevant if MSC ever needs to reproduce pre-2026-01 settlements for
historical Sky-vs-Grove reconciliation.

**Q for Grove (low priority, for completeness):** can Grove document
which pre-MSC months applied the cap and which didn't, and what
triggered it? Useful context for any historical reconciliation; not
blocking any current work.

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

#### S26. Maple syrupUSD vaults (S14, S15) — defensive unwind misclassified as yield loss (LayerZero hack)

**Concrete impact (Spark Apr 2026):** S14 (Maple syrupUSDC) shows
`revenue = −$100.27M` on a position that went `value_som $100.0M →
value_eom $105.3M` (a +$5.3M Δ). S15 (Maple syrupUSDT) shows
`revenue = −$301.35M` on a position that went `$100.0M → $76.9M`
(a −$23.1M Δ). Net Apr drag: ~$400M of fictitious loss, the single
largest contributor to Spark Apr's −$494M `prime_agent_revenue`.

**Root cause — confirmed 2026-05-17.** Spark defensively unwound /
churned both Maple positions in mid-April 2026 (likely **2026-04-19
or 2026-04-20**) in response to the LayerZero hack. The Maple vault
emits a Transfer of the underlying (USDC / USDT) back to the ALM on
redemption; the closed-form yield formula `revenue = Δvalue −
period_inflow` treats those redemption Transfers as principal
**injection** (subtracted from Δvalue) instead of principal
**return**, producing a phantom loss equal to the gross redemption
amount. For S15 specifically: ~$278M of period_inflow on syrupUSDT
shares accounts for the −$301M phantom — Spark cycled (deposit +
redeem) ~$278M defensively through the vault, ending the month with
a smaller net position.

Same root cause as the Grove E2 aHorRwaUSDC phantom-loss patched
2026-04 (`feb_2026_comparison.md` §F5): redemption transfers
misclassified as inflow.

**Methodology fix (no counterparty question).** Mirror the E2 patch:
- Add the Maple vault contract addresses (syrupUSDC, syrupUSDT) to
  Spark's `principal_return_overrides` config so redemption
  Transfers from those addresses are tagged as principal returns,
  not inflows.
- Or detect "redemption" via the share-burn signature on Maple and
  net the underlying-Transfer against it inside the inflow
  classifier.

After the fix, S14 and S15's "revenue" should reduce to actual
pps-driven yield (low single-digit basis points) rather than a
hundred-million-dollar phantom.

**One follow-up Q for Spark/BA (smaller scope):** confirm the
2026-04-19/20 unwind tx hashes so we can pin the
`principal_return_overrides` config to specific events rather than
a blanket vault-address allowlist.

#### S27. Arbitrum POL Cat A — phantom $95M drop turned out to be an RPC archive bug, not a methodology gap

**Original observation (Spark Apr 2026):** S44 (USDS raw Arbitrum POL)
appeared to go `$90.00M → $0` (revenue −$90.00M) and S45 (USDC raw
Arbitrum POL) `$4.44M → $0` (revenue −$5.01M) in our settlement
output.

**Root cause — diagnosed 2026-05-17.** The Spark Arbitrum ALM
(`0x92afd6f2…8709`) actually held **$0 USDS + $0 USDC throughout all
of April 2026**. The $90M USDS / $5M USDC only landed at the ALM on
**2026-05-14 to 2026-05-17** (verified against `arb1.arbitrum.io/rpc`
official endpoint at Apr SoM block 447736930, Apr EoM block
458085623, and intermediate dates 2026-05-02 / 2026-05-12 — all
returned 0 balance). So there was no position drop and no missing
inflow record.

The phantom −$95M came from the **`ARBITRUM_RPC = lb.drpc.live` free
tier returning the CURRENT balance for historical-block
`balanceOf` calls**, instead of the actual historical balance.
Identical pathology to the `MONAD_RPC = lb.drpc.live` issue tracked
earlier — drpc's free tier lacks archive depth and silently returns
"latest" data for any historical block request.

**Fix:** point `ARBITRUM_RPC` at an archive endpoint (e.g.
`https://arb1.arbitrum.io/rpc` for free, or an Alchemy / Infura
archive URL). After the swap + cache invalidation, a Spark Apr
re-run should show S44/S45 at $0 / $0 / $0 and the headline
`prime_agent_revenue` should rise by ~$95M (still leaves the S26
LayerZero withdrawal drag, see Q-S26).

**Not a Spark/BA question** — this is purely on the MSC side. Closing
via the resolved-pointer flow once the RPC swap lands.

### P1 — methodology unknowns affecting accuracy

#### S31. Anchorage May 2026 — confirm the $100M second tranche + over-shoot correction

Per-transfer analysis (Dune query 7690887) of May 2026 escrow ↔ ALM
USDC flows shows Spark deployed a **new ~$100M tranche** to Anchorage
across May 10–20, via four $5M lump transfers plus a streaming
program (~1,100 transfers of $35–132K each):

| Day | Lumps (≥$1M) | Stream (sub-$1M) | Total out |
|---|---:|---:|---:|
| May 10 | −5,000,000 ([0x9f1eea71…](https://etherscan.io/tx/0x9f1eea716acf5c781e19880d98fbe1745ba413c380eed29e941a952673e0dc7b)) | −10,888,888.88 (135 tx) | −15,888,888.88 |
| May 11 | — | −34,111,109.86 (295 tx) | −34,111,109.86 |
| May 13 | −5,000,000 ([0xceb96fc5…](https://etherscan.io/tx/0xceb96fc543437677884e14eb71a115607c7e3faddd466bcd020f3f84c8b3a7ab)) | −270,838.33 (5 tx) | −5,270,838.33 |
| **May 14** | **+5,270,830.00 ([0x1d3dd0ad…](https://etherscan.io/tx/0x1d3dd0adf2b6ab8c1bc89998bc4e370a4549382cf73a16c968c94f49445ad667))** | — | **+5,270,830.00** |
| May 19 | −5,000,000 ([0x8cccc47f…](https://etherscan.io/tx/0x8cccc47f2c87b93153df14040eed93d26edc09222644bc11cf2b4dbb2caad583)) | −4,243,055.55 (91 tx) | −9,243,055.55 |
| May 20 | −5,000,000 ([0x99da9962…](https://etherscan.io/tx/0x99da9962bb5baa6315f36ce952c76ef5e3854df0c956969433e4d05ccbc53b80)) | −35,743,055.51 (602 tx) | −40,743,055.51 |

**Σ outflows = $105,270,838.70; minus the May 14 return = $100,000,008.70
≈ exactly $100M.** Reading: the May 13 flows overshot the tranche
target; Anchorage returned the excess ($5,270,830, = that day's
receipts less $8.33) the next morning, landing the net deployment on
$100M. Corroboration: the Jun 3 interest sweep jumped to $1,435,965 ≈
$891,780 (original $150M tranche) + ~$544K (≈7.13% APR on $100M for
the partial month).

(May 4's +$891,780.28 is the regular monthly interest sweep on the
original tranche.)

**Our current treatment** (config `principal_return_overrides`, added
2026-06-10): the May 14 inflow is registered as a capital return, NOT
yield. All outflows stay capital under the directional default. Net
May Anchorage yield books as **$891,780.28** (the May 4 sweep only).

**Questions for Spark:**
- Confirm the new ~$100M tranche (terms? same 7.13% APR? same
  June 16 termination as the original $150M, or its own schedule?).
- Confirm the May 13 → May 14 out-and-back was an over-shoot
  correction to land the tranche on exactly $100M.
- Is the Jun 3 +$1,435,965 sweep pure interest (consistent with
  $250M total principal)?
- Will the termination sweep(s) separate principal from final
  interest, or arrive combined? (We need the split for
  `principal_return_overrides` — see the placeholder note in
  `config/spark.yaml`. Note the amounts: $250M total if both tranches
  unwind together.)

#### S30. Savings V2 VSR liability — confirm it is outside MSC `prime_agent_revenue` scope

As of PR #126, MSC treats the Savings V2 vaults (S56/S57/S59/S60) as
**position-only**: the gross yield on vault-deployed capital remains in
`prime_agent_revenue` (it is earned at the ALM and captured by the
existing S1–S55 venues), but the depositor-side VSR liability accrual
is **not** subtracted. Rationale: the MSC accounting boundary is the
ALM proxy — depositor deposits/withdrawals (principal + accrued VSR)
are capital flows in/out of the ALM, and the VSR is a vault-layer
obligation of Spark's retail product, not an MSC settlement item. See
`docs/spark/PRD_savings_vaults.md` §3.

Question for Spark: confirm this scope reading of
`prime_agent_revenue`. Two consequences to be explicit about:

1. MSC's headline includes yield earned on depositor-funded capital
   (with no VSR offset and no CoF allocated against it — it was never
   drawn from the ilk).
2. MSC will diverge from Spark's own surplus accounting
   (`deployed × (apr − borrow_cost)`) and from the BA Labs
   balance-sheet headline by ≈ the period's VSR accrual
   (~$1.8M for 2026-01; ~$4–5M/month at 2026-06 TVL). We will carry
   this as a documented scope difference unless Spark prefers the
   VSR-netted convention, in which case the (previously implemented)
   per-vault negative VSR line is restored.

#### S28. `cum_debt` base — frob-only or frob+grab (Vat `Art`)?
Settlement currently uses `cum_debt = (Σ Vat.frob.dart + Σ Vat.grab.dart) × Vat.ilks[ilk].rate / 1e27` as the BR principal — i.e. the canonical Vat `Art × rate`. This includes `grab` darts. In the Allocator system `grab` is used for stability-fee capitalisation, **not** liquidation: each call bumps the urn's normalised debt to record interest the vow has accrued (the paired `vat.suck` on the vow side, which our SQL doesn't watch, supplies the matching `dai[vow]` credit). Grove's xlsx "Subscriptions" column has historically been frob-only.

For Spark, cumulative `grab` dart was ~$48M by 2026-04-30 (see `src/settle/queries/debt_timeseries.sql:43-46`). Treating grab-inclusive `Art` as the "Subscriptions" base means we charge BR on capital that Grove's frob-only tally would exclude.

Question: which base is canonical for the CoF / Subscriptions split? Options:
1. **Grab-inclusive (current implementation):** matches on-chain Vat state exactly; consistent with `Vat.ilks(ilk).Art`; consistent with Grove if Grove also moves to grab-inclusive.
2. **Frob-only:** matches Grove's historical reporting; intentionally excludes capitalised interest from BR principal.

If we land on #1 across all primes, no further action. If #2 for Grove and #1 for Spark, the SQL comment block at `debt_timeseries.sql:43` should make the asymmetry explicit and the methodology doc should call it out.

Until confirmed, settlement runs include grab-inclusive `Art` — flagged inline in the SQL so anyone touching the query sees the open item.

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

**Update from Spark interview (2026-05-06):** Spark confirmed that
"some venues send gains directly to the ALM Proxy" beyond just
Anchorage — i.e. the current single-address allowlist is almost
certainly incomplete. Sharpened ask: please **enumerate** the venues
whose gains arrive at the ALM directly, and provide the **sender
address(es)** for each so we can extend `external_alm_sources`. Until
this list is in hand, every par-stable inflow we can't trace to a
known capital flow is ambiguous between yield (should count as Cat A
revenue) and capital movement (should NOT).

#### S14. sparkPrimeUSDC1 (S18) — Arkis API NAV vs on-chain `convertToAssets()`
Persistent ~0.7% drift vs Spark's view at all 3 EoM dates. Spark's
amounts are suspiciously round ($15.00M / $10.10M / $10.10M), suggesting
the API NAV is rounded. Is the on-chain `convertToAssets()` authoritative
for revenue recognition, or should we consume Arkis's API NAV (which
Spark uses)? Bias on our prime_agent_revenue: +$60–100K/quarter
over-statement.

**Update from BA call #2** (see PRD §17.13): BA themselves don't
currently consume the Arkis API directly — Arkis exposes total
position value via API but BA needs Spark to facilitate access.
Implication: BA's own Arkis numbers may also be derived from the
on-chain `convertToAssets()` (or from Spark's manually-shared
workbook), not from Arkis directly, so BA isn't an independent
authority for this venue. Treat the Arkis-vs-on-chain question as
needing direct input from Spark / Arkis rather than from BA.

#### S21. Distribution rewards — Cowswap ref code 1003 + in-range Spark codes (100–999)
Raised in Spark team interview (2026-05-06). Spark identified two
distribution-rewards streams:

- **Cowswap reference code 1003** — outside Spark's normal ref-code
  range (i.e. assigned to Cowswap as a distinct program).
- **Spark codes in range 100–999** — Spark's own in-range ref codes
  (mechanism not detailed in the interview — presumably referral /
  liquidity incentive codes earned by Spark on third-party
  platforms).

Today MSC reports `MonthlyPnL.distribution_rewards = $0` for Spark
(Phase-3 placeholder per PRD §17.6, populated only when a real
source lands — historically only for Skybase). Lighting these up
requires:

**Q for Spark (counterpart of Grove's G20 FundingMorpho ask):**

1. **Cowswap (code 1003):**
   - Contract address(es) where Cowswap accrues / distributes
     rewards to Spark.
   - Read semantics — `claimable()` view, `Transfer` events,
     periodic on-chain sweeps to ALM Proxy as a stable token, or
     off-chain dashboard?
   - Eligibility window — was Spark eligible from a specific date
     (2026-01 MSC start? earlier?) and any termination date?

2. **In-range codes (100–999):**
   - Are these all the same mechanism with different IDs (in which
     case one feed covers them), or distinct programs that need
     separate readers?
   - List the codes that have actually accrued non-zero rewards in
     2026-Q1 + the corresponding payer addresses / contracts.
   - Same read-semantics question as above.

If any stream sweeps to ALM Proxy as a stable token, the existing
`external_alm_sources` plumbing handles it under Cat A (no new code).
If accrual lives at a contract that needs to be queried (claimable /
events), MSC needs a new `IDistributionRewardsSource` adapter — same
shape as G20.

#### S23. PSM3-USDC SDE pattern — implement per-leg split or accept the gap?
`config/sky_direct_exposures.yaml` declares both Spark and Grove as SDE
on the **USDC leg** of L2 PSM3 holdings (`pattern: 'psm3_usdc_non_ethereum'`,
matching Atlas §A.2.3.2.2.3 *"USDC in PSM3 on non-Eth chains"*). But
`src/settle/domain/sde.py` logs a WARN at load that pattern entries are
not consumed by `compute_sky_revenue` — they only document the methodology
intent. PRD §17.10 acknowledges this as an accepted gap, but it is
**Spark-material**:

- **Today's pipeline**: Spark's full L2 PSM3 USDS-equivalent (~$544M as
  of 2026-05) is subtracted from `utilized` in `compute_sky_revenue`,
  so Spark gets full BR-reimbursement on the entire amount and Sky
  collects no SDE-direct revenue on the USDC slice.
- **If Atlas is canonical**: the USDC slice should be SDE — Sky takes
  the actual yield on that capital, and `utilized` should only deduct
  the non-USDC slice (USDS + sUSDS holdings inside PSM3).

To implement the split correctly we need:
1. Per-leg balance read inside PSM3 (`USDC`, `USDS`, `sUSDS` reserves
   separately — PSM3 has `totalAssets()` + per-asset methods, or we
   can read each underlying token's `balanceOf(psm3)` and apportion
   the prime's `shares × convertToAssetValue` across legs by reserve
   weight).
2. New `IPsm3LegReader` source (or extension to `IPsm3Source`) that
   surfaces the 3 legs.
3. SDE pattern handler in `compute_sky_revenue` that reads the USDC
   slice and routes it to `sde_asset_value` instead of `psm_usds`.

**Q for Spark/BA/Sky:** confirm Atlas §A.2.3.2.2.3 is the canonical
intent (Sky claims actual yield on PSM3 USDC), and that the per-leg
split is the right implementation. Until then Spark Q1 2026 numbers
are computed with the full PSM3 holdings BR-reimbursed (i.e. Spark
favorable, Sky under-credited).

**Implementation landed 2026-05-11 (issue reopened — pending Spark/BA/Sky
methodology sign-off):** per-leg split now live in
`get_psm_usds_timeseries` (ERC4626_SHARES branch). PSM3's
`convertToAssetValue(spark_shares)` is decomposed daily into USDC + USDS
+ sUSDS legs and routed per PRD §17.11:
- USDS  leg → subtracted from `utilized` (BR-reimbursed)
- USDC  leg → added to `sde_asset_value` (Atlas SDE — Sky takes actual yield)
- sUSDS leg → stays in BR base; orchestrator credits 30 bps × value
  × n_days as Prime Revenue, neutralising the SSR + BR composite
  (Rule 5 same shape — economic neutrality on idle sUSDS)

Resolution will move this entry to `## Resolved` once Spark / BA / Sky
confirm the leg-split routing matches their reading of Atlas
§A.2.3.2.2.3 + the neutrality intent for idle sUSDS.

#### S24. SDE mid-period activation — full-month vs day-level pro-rating
Raised in PR #67 review (2026-05-11). The PR documents (in
`config/sky_direct_exposures.yaml`) a known limitation: the compute
layer queries `SDETable.is_active_on(period_start)`, so an SDE entry
whose `start_date` falls inside a month is either applied for the
**full month** or **skipped entirely** — never pro-rated from the
actual start_date to month-end.

**Concrete impact today:** Spark S24 USDT SDE (start 2025-11-13). Per
`is_active_on(2025-11-01)` the entry is not active at SoM → Nov 2025
is treated as fully non-SDE. First counted SDE month: Dec 2025. Sky
is under-charged ~13 days of USDT SDE utilisation in Nov 2025
(roughly: position_value × subsidised_BR × 13/365).

The PR comment intentionally **defers the fix** (PR scope is logging
/ extraction optimisations, not methodology). The dedicated SDE-
alignment branch is the right place to land day-level pro-rating.

**Q for Spark / Sky governance / BA:**
1. **Authoritative semantic** — is the canonical methodology
   "full-month if active at SoM" (matches Grove team's workbook
   `sd_share` lock-at-SoM convention) or "day-level pro-rating from
   the actual start_date"? Atlas branch `add-sky-direct-exposure-
   start-dates` lists start dates without saying which convention
   to apply.
2. **Symmetric for end_date** — if pro-rating is canonical, does it
   apply on the closing side too (Historical JAAA on Eth ended
   2026-03-12, mid-month; today Mar 2026 is fully SDE since the
   entry was active at SoM Mar 2026).
3. **Retro impact** — if day-level is canonical, do prior settlements
   (Nov 2025, Mar 2026 for JAAA-end) need to be re-issued, or is the
   prospective fix sufficient?

If day-level is confirmed, the fix is ~10 lines in `compute_sky_
revenue` plus a regression test that covers a SoM-not-yet-active
venue + a within-month end_date.


### P2 — sanity checks / confirmations


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

#### S19. SparkLend reserve factor — confirm 10% stays at protocol level (not Prime Agent revenue)
Raised in Spark team interview (2026-05-06). Spark stated "10% of
the yield goes to reserve factor" on USDS supply to SparkLend
(spUSDS / spUSDT / spDAI / spPYUSD venues — S1 / S3 / S4 / S5).
Recurrent reserve-factor actions are executed via spells.

Our reading: the reserve factor is **SparkLend protocol** income —
not Spark Prime Agent income. The supply rate Spark receives on its
spTokens is already the **net** of the reserve factor (Aave-style
accounting: `supply_rate = borrow_rate × utilization × (1 −
reserve_factor)`), so MSC's existing Cat C `scaledBalanceOf ×
liquidityIndex` accounting captures Spark's prime-agent share
correctly. The 10% accrued to the reserve factor stays at the
protocol level and does NOT flow back to Spark's prime-agent
revenue.

**Q for Spark (confirmation):** is the above reading correct? In
particular:

1. The reserve factor accrues to the SparkLend protocol treasury,
   not to Spark's ALM Proxy or subproxy — i.e. it should NOT appear
   anywhere in Spark Prime Agent revenue accounting.
2. There's no separate "reserve factor distribution" event later
   that returns the accrued amount to the prime.

If either is wrong (i.e. the reserve factor IS flowing back to the
prime), MSC under-counts Spark prime revenue today by ~10% × yield
across S1 / S3 / S4 / S5.

#### S20. SparkLend "large positions trigger negative returns" — at what threshold?
Raised in Spark team interview (2026-05-06). Spark noted that
**large positions on SparkLend trigger negative returns** for the
supplier, but the reserve factor still gains. Mechanism unclear from
the interview note — could be (a) the supply-rate utilisation curve
flips below the reserve-factor cut at low utilisation, (b) a
borrow-rate cap mechanism that creates negative net yield for the
supplier, or (c) a position-size-specific penalty in SparkLend's
rate model.

**Q for Spark:**

1. What's the precise mechanism — utilisation curve, rate cap,
   position-size penalty, or something else?
2. At what threshold (position size in USD, utilisation %, or other
   trigger) does this kick in?
3. Are any of Spark's current spToken positions (S1 spUSDS $156M,
   S3 spUSDT $616M, S4 spDAI $257M, S5 spPYUSD $100M) close to that
   threshold — i.e. should MSC expect negative-return periods on
   any of these in the near term?

Material if a Q1/Q2 month sees a negative supply rate on one of the
large spTokens — our `scaledBalanceOf × liquidityIndex` accounting
would correctly reflect the loss, but operators should know to
expect it rather than treat it as an anomaly.

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

#### S22. Anchorage fee structure — what does Anchorage charge before sweeping to ALM?
Raised in Spark team interview (2026-05-06). Spark confirmed
Anchorage charges fees on the principal-allocated position (S23 in
config — registered escrow at
`0x49506C3Aa028693458d6eE816b2EC28522946872`), but the fee structure
wasn't detailed. Yield arrives on-chain as USDC sweeps from the
escrow to the SLL ALM, already net-of-fee.

**Status: operational, doesn't shift numbers today.** Our existing
`external_alm_sources` accounting captures the **net** USDC arriving
at the ALM, which already reflects whatever fees Anchorage has
deducted upstream. MSC's `prime_agent_revenue` is correct as long as
the fee is taken pre-sweep.

**Q for Spark (low priority, for completeness):** what's
Anchorage's fee structure (% of yield, % of AUM, flat fee)? Useful
for projecting expected sweep size relative to the principal × APR
(~7.13% gross APR observed Q1) and for spotting any month where the
sweep deviates materially from the expected net.

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
Our snapshot reads STAC at $1.0172 via Chronicle (`0x9d77…58b`,
reflecting real CLO yield accrual). Your `/allocations/?star=grove`
reports STAC at $1.00 flat. Per `docs/pricing/allocation_pricing.csv`
STAC has Chronicle as Oracle1 and Redstone (`0xedc6…d7d`) as Oracle2.
Are you using Redstone, const_one, or a different feed? If const_one is
canonical, we should switch our NAV path to match (currently whitelisted
as "known divergence" — drift ~1.7%).

**Update 2026-05-12:** Read both Chronicle and Redstone at block
25,078,418 — Chronicle returns **$1,017.2039**, Redstone returns
**$1,017.6458** (delta 4 bps, well within feed-noise). Both agree that
STAC is currently ~1.7% above $1,000 par. BA's const $1.00 is the
outlier; MSC has wired up Redstone as a registered NAV source
(`RedstoneNavSource`) and switched E7's fallback chain from
`const_1000` to `redstone` to remove the static-placeholder risk. The
remaining open question is whether BA intends to migrate to one of the
on-chain feeds or keep const_one as policy.

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

#### B14. Gain-realization double-counting — does Atlas need a "delay realisation" rule?
Raised in BA call #2 (see PRD §17.13). Concern: under daily-NAV
accounting, unrealised gains on a position are recognised period after
period as the venue's mark-to-market value rises. When the position is
finally unwound (sold / redeemed at a different price than the last
NAV), the realised PnL is recognised AGAIN against the same economic
gain, giving Sky two bites at the same revenue.

BA acknowledged the risk but didn't give a closed answer: their note
was "Atlas changes/rules possibly needed here, delay of realising
gains" — i.e. an Atlas-level policy fix to defer revenue recognition
for unrealised gains until the unwind would be the cleaner solution,
but no such rule exists today. This isn't a bug in our pipeline (we
faithfully execute the documented NAV methodology); it's a
methodology-level open question that affects every NAV-priced venue
(RWA tranches, vault shares with non-par PPS, LP positions priced via
oracles).

**Q for BA / Sky:**
1. Is an Atlas update on gain-realisation timing on the roadmap, and
   if so, what's the expected semantics — period-by-period NAV (status
   quo, accepts the double-count risk) vs. cost-basis-until-unwind
   (defers recognition) vs. some hybrid?
2. In the meantime, should MSC flag any month where a previously-NAV-
   priced position is unwound, so the realisation event can be
   reconciled manually against prior monthly recognitions?

Material whenever a Cat E (RWA NAV) or Cat F (LP) position is
liquidated mid-cycle. Not yet observed in Q1 2026 (no major unwinds);
becomes load-bearing the first time it happens.

Cross-reference: **B17** asks a related-but-distinct question — when
a NAV gain materialises into stable USDS at the ALM, is BR charged
on the enlarged USDS position (i.e. is Sky paid yield on already-
recognised yield)? B14 is about whether the gain is recognised
twice; B17 is about whether BR applies to the realised gain once.

#### B15. T-Bill rate — tenor + sampling frequency unspecified in Atlas
Atlas A.2.8.2.2.2.2.2 (Borrow Rate Mechanism) specifies the subsidy
formula as:
```
subsidised_rate = t-bill_rate + (base_rate − t-bill_rate) × T/24
```
over the first $1B of utilized USDS for 24 months from 2026-01-01 (T =
elapsed months). Two parameters are NOT defined in the spec text:

**(a) T-Bill tenor.** The US Treasury publishes daily yield curve points
at 4-week, 1M, 3M, 6M, 1Y, 2Y, … which differ by tens of bps. Grove's
Feb 2026 PnL workbook empirically used **3M T-Bill** (3.67–3.74%)
matching `treasury.gov/daily_treasury_yield_curve`'s `bc_3month` field.
Is 3M canonical for both primes, or does Sky intend a different tenor
(e.g. 1M for short-end alignment with overnight borrowing, 1Y for
duration-matched against the 24-month subsidy program)? The choice
shifts the subsidy ~10–30bps per year.

**(b) Sampling frequency.** The Atlas formula reads `t-bill_rate` as
a single symbol. Three plausible interpretations:

1. **Daily** — read fresh from the Treasury daily yield curve each
   settlement day; matches `subsidised_apy_d = ref_rate_d + (base_apy
   − ref_rate_d) × T/24` in `config/subsidy_reference_rates.yaml`
   (current MSC behaviour).
2. **Monthly snapshot** — read once at the start of each settlement
   period and held constant for the month.
3. **Program-start snapshot** — read once on 2026-01-01 and held
   constant for the entire 24-month subsidy program. Frozen value
   simplifies disputes but ignores rate moves.

Q1 2026 Treasury 3M T-Bill moved ~7bps (3.67% → 3.74% → 3.67% range);
small for Q1 but a regime change later in the 24-month window could
diverge materially across the three interpretations.

**Q for BA / Sky:** what tenor and sampling frequency does Sky
intend? If Atlas is silent by design (both left to discretion), MSC
will pin to 3M / daily and document the choice; if Sky has a specific
intent, it should be added to the Atlas text.

Coupled with **S5** (whether Spark uses T-Bill at all, vs the EFFR
note) — these three together are the full subsidy-reference-rate
specification gap.

**Update from Grove interview (2026-05-06):** Grove reconfirmed **3M
T-Bill** as the tenor on their side, which (a) matches our existing
config and (b) suggests 3M is canonical for both primes (still needs
Sky's confirmation that it's the universal default vs. per-prime
choice). Sampling frequency (daily / monthly snapshot / program-start
snapshot) remains unresolved. The Grove interview also surfaced an
ambiguous "send value calculations for 3-month windows" note — see
**G5** for the open question on whether that means quarterly
settlement of the subsidy.

#### B16. TGE Penalty — when and how was it applied to Grove?
Per Atlas A.2.8.2.2.2.7.1.1 (Prime Token Generation Event) +
A.2.8.2.2.2.7.6 (Income Definition) + A.2.8.2.2.2.7.1.2 (Token Launch
Penalty Settlement): if a Prime did not complete its TGE by
**2025-07-01**, a **30%** penalty applies on the Prime's "income"
accruing until TGE happens. "Income" is defined as:

- (i) Distribution Rewards (A.2.8.2.2.2.3.1)
- (ii) Distribution Reward Bonus for 2025 (A.2.8.2.2.2.3.2)
- (iii) Platform Fees charged to users
- (iv) Real World Asset fees (origination, servicing, related)
- (v) Blended cost of allocation spread between Junior and Senior
  Risk Capital

Spark completed its TGE before the deadline; **Grove has not** as
of today (2026-05-06), so the 30% penalty has been accruing for
~10 months when MSC settles Q1 2026. MSC's current pipeline does
**not** model this penalty — `prime_agent_total_revenue` is
reported gross (no 30% deduction). If BA's published Grove numbers
DO apply the penalty, our reconciliation will be off by ~30% of the
applicable income components every settlement until Grove TGEs.

**Q for BA:**

1. Has the penalty been applied to any settlement so far? Which
   periods, and from what start date — was it (a) accruing from
   2025-07-02 with retroactive recognition once MSC started, (b)
   only from the 2026-01 MSC start, or (c) deferred to a separate
   "Token Launch Penalty Settlement" (per A.2.8.2.2.2.7.1.2) outside
   the monthly cycle?
2. **How is "income" mapped to MSC line items?** Specifically: does
   the 30% apply to (a) just `distribution_rewards` (today $0 for
   Grove — the field is a Phase-3 placeholder), (b) all of
   `prime_agent_total_revenue`, (c) only the components Atlas
   enumerates that map to actual revenue on Grove (Platform Fees,
   RWA fees), or (d) something else? The "blended cost of allocation
   spread between Junior and Senior Risk Capital" component
   (Atlas (v)) doesn't have an obvious mapping to our pipeline —
   we'd need BA's interpretation.
3. Where does the penalty appear in BA's settlement output —
   netted from `prime_revenue`, added to `sky_revenue`, or surfaced
   as a separate line item? (Affects how MSC reports it once we
   model it.)
4. Is there a known TGE target date for Grove that would let us
   project when the penalty stops accruing?

If BA confirms the penalty IS being applied, this becomes P0 —
MSC needs a `tge_penalty` line item on `MonthlyPnL` (Grove only,
date-bounded against TGE completion) and a re-run of all Grove
settlements since 2026-01.

#### B17. Base rate on realised gains — does BR apply to NAV-derived USDS?
Raised in Spark team interview (2026-05-06) as an open methodology
question on Spark's side ("Pay base rate on realised gains?"). When
a NAV-priced position (Cat E RWA tranche, Cat F LP, Cat C
principal-bearing aToken with index growth) appreciates over a
period and is then realised into stable USDS at the ALM Proxy, the
prime's USDS holdings grow by the realised gain. That gain is
**already** recognised as `prime_agent_revenue` in the period it
materialised (per the NAV methodology). Question: in the **next**
period, does Sky charge BR on the larger USDS position — including
the portion that came from already-recognised yield?

The two readings:

- **(a) BR on full ledger debt.** Sky charges BR on `cum_debt`
  (every USDS the prime has drawn against the ilk), period. Realised
  gains net to `utilized` only via the "idle USDS at subproxy/ALM
  netted from utilized" mechanic, so BR applies only to deployed
  capital, not to gains that sit idle. **Matches MSC pipeline today.**
- **(b) BR on realised gains explicitly.** Sky charges BR on the
  enlarged USDS holding regardless of where it came from — i.e. the
  realised gain creates a new BR liability the moment it lands.
  Would mean MSC is under-counting `sky_revenue` after any
  significant gain realisation.

**Q for BA / Sky (arbitrating):** which is canonical?

Cross-reference: **B14** is the sibling question on whether the
gain itself is recognised twice (period-by-period NAV vs.
cost-basis-until-unwind). B17 is about whether the *already-
recognised* gain attracts BR going forward. Both close to a clean
answer once Sky picks a position on gain-realisation timing — but
they're independently load-bearing.

Material whenever Cat E / Cat F positions accrue meaningful gains
that are then realised into the ALM. Q1 2026 had no large
realisations; becomes load-bearing the first quarter that does.

### P2 — sanity checks / confirmations

#### B11. Agent rate — does BA include an equivalent component in prime revenue?
MSC reports `prime_agent_total_revenue = prime_agent_revenue + agent_rate +
distribution_rewards`, where `agent_rate` is what the prime EARNS on its
subproxy's idle USDS/sUSDS. Formula (`src/settle/compute/agent_rate.py`):

- USDS in subproxy: rate = **SSR + 20bps** APY, applied daily to
  cumulative USDS balance.
- sUSDS in subproxy: rate = **20bps** APY only, applied daily to the
  **cost-basis principal** (`shares × entry_pps`) — SSR is already
  earned via the sUSDS index growth, so applying SSR again would
  double-count.
- Daily compounding, summed over the settlement period.

For Q1 2026 this is a small but non-zero contributor to the headline
(idle subproxy holdings × ~20bps over the period). Two things to
confirm with BA:

1. Do BA's reported prime-revenue numbers include an analogous "agent
   rate" component for subproxy idle USDS/sUSDS, or is prime revenue in
   BA's view limited to per-venue yield?
2. If included, does the formula match — specifically, is the sUSDS
   leg priced on **cost-basis principal** (no SSR double-count) and
   is the rate-over-SSR component **20bps**?

If BA omits this component or uses a different rate, our headline
`prime_agent_total_revenue` will diverge from BA's published prime
revenue by the agent-rate amount each month.

**Update from BA call #2** (see PRD §17.13): BA's Q1/Q2 answers said
that "idle assets count only Sky side" (i.e. idle USDS / sUSDS is
netted out of `utilized` so the prime is reimbursed BR, but does NOT
generate Prime Agent revenue in BA's view). This is in apparent
tension with the prime-settlement-methodology Step 3 agent-rate stream
that our pipeline implements. Two possible reconciliations:

- **(a)** BA's "idle" refers only to **ALM-side** idle holdings (which
  we also don't credit as prime revenue — we only credit subproxy-side
  USDS/sUSDS per the methodology doc). In that case there's no
  conflict and B11 is purely a confirmation question.
- **(b)** BA's "idle" includes **subproxy-side** holdings too — in
  which case BA's Prime Agent revenue is structurally ~`agent_rate`
  smaller than ours and we have a real divergence.

Sharpened ask for BA: please disambiguate (a) vs (b) explicitly.
Specifically, for the SLL subproxy on Ethereum holding USDS / sUSDS
during Q1 2026, did BA's reported Prime Agent revenue include the
SSR+20bps (USDS) / 20bps-on-cost-basis (sUSDS) accrual on those
balances, or did it skip that component entirely?

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
PSM3 reads via `IPsm3Source` (`extract/rpc.py::psm3_shares` +
`psm3_convert_to_asset_value`), ALM-side raw balances via the venue
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

### S5. Subsidy reference rate — Atlas says T-Bill, current config says EFFR
**Resolved 2026-07-30** via [#25](https://github.com/soterlabs/settlement-cycle/issues/25). See `PRD.md §17.13`.

### G2. External ALM sources / off-chain yield distributors
**Resolved 2026-06-01** via [#2](https://github.com/soterlabs/settlement-cycle/issues/2). See `PRD.md §17.13`.

### S25. L2 sUSDS spread-credit silently $0 (S37/S43/S47/S51) — Prime Revenue under-count
**Resolved 2026-05-18** via [#75](https://github.com/soterlabs/settlement-cycle/issues/75). See `PRD.md §17.13`.

### S1. Confirm `2024-11-18` is Spark's billing anchor
**Resolved 2026-05-07** via [#19](https://github.com/soterlabs/settlement-cycle/issues/19). See `PRD.md §17.13`.

### S3. Anchorage S23 — $150M tri-party loan, on-chain addresses confirmed
**Resolved 2026-05-05** via [#17](https://github.com/soterlabs/settlement-cycle/issues/17). See `PRD.md §17.13`.

### Pre-Q-ID resolutions

These predate the stable Q-ID scheme; tracked here as compact pointers,
full narrative in `PRD.md §17`.

- **Subsidised borrowing rate (formula + ramp).** Resolved 2026-05-02
  per Sky governance — formula `ref_rate + (BR − ref_rate) × T/24`,
  program start 2026-01-01, cap at first $1B utilized. See
  `PRD.md §17.13` (medium-priority list, item 6). Residual
  reference-rate questions are open as **G5** / **S5** / **B15**.
- **PSM3 daily sampling (Spark non-Eth chains).** Resolved 2026-04-30
  — Base + Optimism live ABI confirmed; Arbitrum + Unichain assumed
  same shape; selectors `0xce7c2ac2` (`shares`) + `0x41c094e0`
  (`convertToAssetValue`) plumbed. See `PRD.md §17.13` (Spark Q1
  resolved list, item 3).
- **Hardcoded EoM blocks (`spark_fixture_loader.py`).** Identified
  2026-05-04 as a Q1-2026-only code path that silently skips Cat A
  for any other month. Tracked as internal TODO, not a counterparty
  question. See `PRD.md §17.13` (Spark Q1 resolved list, item 6).
- **Foundation USDS holdings.** Resolved during 2026-05-04 dashboard
  re-review — Foundation appears in
  `result_spark_usds_s_usds_usdc_in_psm_3_curve_psm_3_proxy_foundation_aave`
  (Eth, ~$1.1M) and is captured implicitly via existing PSM/ALM-raw
  reads. See `PRD.md §17.13` Code-review acks (B1 reverse-engineering)
  and **B1** for the residual `idle_assets` mapping question.
- **GACLO-1 valuation.** Resolved earlier — Galaxy CLO position
  priced via the standard Cat A par-stable accounting on the USDC
  sweep that lands at the ALM on the 10th of each month (no
  on-chain principal-side feed needed). See `PRD.md §17.13` Grove
  team interview (2026-05-06) edge-cases.
- **~$1.13M Sky-Share residual (Grove Mar 2026).** Largely closed
  by 2026-05-02 work (subsidy + SDE refactor + `pricePerShareFeed`
  NAV); residual ~$45K excluding the E1 Horizon rewards channel
  (tracked under **G3**). See `PRD.md §17.13` (medium-priority list,
  item 5).

