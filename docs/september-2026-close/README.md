# September 2026 close validation

Status: all six primes have 30 September API dates. The original six
API/full-month comparisons passed exactly at the baseline revisions recorded
below. **The current settlement reports now include an isolated S1/E10 refresh
and no longer match those older Spark/Grove API revisions.** Other venue
revenue is unchanged. API publication is a follow-up task; see
[`../PRD_selective_venue_revenue_refresh.md`](../PRD_selective_venue_revenue_refresh.md).

September reports cover all six primes, non-MSC, consolidated Sky and TMF.
Spark/Grove use the official September 30 SOFR of 3.90%. The current branch
includes merged PR #218. The details below about original API comparisons
are baseline evidence, not a claim about the newly refreshed artifacts.

The DR submodule is pinned to merged PR #27 (`1e9ecb2`). Normal September
accrual uses the finalized workbook plus its full-precision companion CSV,
with checksums and cent-rounded workbook reconciliation. The redundant independent DR replay was stopped on October 1 at the
operator's request. The finalized settle-dr-dune output is the source;
settlement-cycle validates its import and attribution without replaying DR. The four approved historical
Skybase payment corrections total **124,694.330541 USDS** and appear separately
from September-earned revenue. See `proposed-skybase-trueups.md` for details.

The original September 30 API calculations were compared with separate
full-month `compute_monthly_pnl(prime, Month(2026, 9))` executions, with
`as_of` omitted, using calculation commit
`c32f25a7ef410eaaf9212b8f546f652c715dc7ac` (merged PR #222).
For the official SOFR refresh, Grove completed fresh API and monthly runs.
Spark's redundant venue replays were stopped: borrowing costs were instead
recalculated from the saved daily inputs of those previously validated API
and monthly results. No new chain reads are needed for this rate-only change.
The canonical interest formula confirms zero monetary change, since both
3.88% and 3.90% exceed the September 30 Base Rate of about 3.742%.

The comparison checks every result field. Each JSON records the API revision,
input-version agreement, differences and complete-result hashes. Spark, Grove, Keel,
Skybase, Osero and Obex all have identical hashes. Distribution rewards
are added from the separate September DR workbook when assembling settlement
reports; they are not part of this supply-revenue comparison.

## Reference rate

The October 1 refresh replaces the authorized September 29 carry with the
official September 30 observation. Spark and Grove now have
`coverage_complete: true`, without operator-estimate provenance. September
1–29, all other primes, and all supply-side/venue revenue remain unchanged.
The affected API results are compared with the refreshed monthly results;
borrowing costs are also independently revalidated from daily inputs.
See `official-sofr-refresh.json` and `official-sofr-report-validation.json`.

## Grove Basin deduction

PR #222 applies only from September 1, 2026 and only to ALLOCATOR_GROVE_A.
The September 1–29 API transformation changes borrowing-cost fields and their
resulting PnL; supply-side revenue is unchanged. The September 30 calculation
includes the deduction directly.

For the full month, Grove borrowing interest falls from
3,873,580.395582812200319233744 to 3,754,292.984290454805445993200 USDS:
a reduction of **119,287.411292357394873240544 USDS**. Its 2,507,467.934719144391934655300
USDS SDE claim is separate from borrowing interest.

## Non-MSC and Gelato

September non-MSC net revenue is **708,599.21131559140975324007 USDS**.
The legacy DSR expense includes closing unpaid interest less opening unpaid
interest; it does not drop the tail after the last September `Pot.drip`.

Gelato returned **42,469.146527 DAI** to MCD_BLOW2 on September 7. The refund
was already moved to the surplus buffer on September 15. Recognition is at
receipt, and the subsequent cash recognition is offset, so September contains
the refund exactly once. The September non-MSC report links both transactions.

No pre-September settlement reports are regenerated and no on-chain
transaction is submitted. TMF parameters are calculated proposals, not a
claim that an October executive spell has executed.

## Operational recovery

HyperSync requests are paced across the close jobs. Distribution-reward page
caches and chunk checkpoints allow retries to preserve completed work.
Spark event timestamps were prefetched from fresh complete HyperSync log
queries, retaining only blocks outside the observed finality margin.
Historical `balanceOf` and `scaledBalanceOf` RPC reads were also batched and
stored under the existing finalized input keys, with no overwrites. An
alternate archive endpoint was cross-checked against 40 exact historical
reads before use. Neither operation approximates balances or changes yield
formulas; failed responses are never cached as zero.

The first DR chunk began before the extraction-cache improvements. It was
allowed to finish to preserve its work; later chunks use the updated extractor.
The reward and attribution formulas are unchanged. The conversion cutoff
change removes only later-day rows that the existing calculation already
filtered out. The output workbook is assembled with the pinned DR revision.

Non-MSC was calculated at commit `4506950`; later report changes clarify the
savings narrative and omit a holder split unavailable from the source.
`non-msc-validation.json` records the final totals and refund events, while
`dsr-accrual-check.json` records the independent boundary-state calculation.
The two DSR results differ only in the final Decimal-context digit (far below
one cent).

## Calculation-log review

Agreement between the two executions is supplemented by checking their
warnings:

- Grove E9's opening position includes the pending-redemption escrow.
  Opening value is 858,067,027.2826399485163006316, closing value is
  309,460,827.1912790929082352869, and net capital inflow is
  -550,048,184.446080 USDS. Their difference gives the booked
  1,441,984.3547191443919346553 USDS revenue. The SDE diagnostic previously
  compared the wallet-only opening balance against the escrow-inclusive
  value; this PR corrects that diagnostic without changing the deduction.
- Spark S26 has zero opening/closing idle USDC and 984,135 USDS of external
  income subsequently deployed. Its negative capital-only time-weighted
  balance is clamped to zero; it does not represent a revenue loss.
- Spark S63 is a position-only retail vault on Robinhood, outside MSC revenue.
  Its missing chain pins omit the position-only display, not a settlement
  revenue component. This existing presentation limitation is unchanged.
- Plain-token aToken probes can revert; the guarded RPC balance path remains
  in use. No failed provider read was substituted for a zero balance.
- The comparison runs intentionally stage reports without DR. Final report
  assembly uses the finalized September reward workbook. Codes without confirmed
  ownership are explicitly withheld and disclosed, never assigned by number range.

## Reward attribution

`dr-attribution-pending.json` records the finalized full-precision partition
between payable rewards, intentionally non-payable codes, retired Keel rewards,
and unresolved ownership. Codes 123/232/234/3003/3123 remain withheld.
Codes 1997–1999 and 1020 resolve to Skybase; Grove Farm code 2009 resolves
to Grove. Codes 99/10000/10001/-999999 remain unpaid.

All source accrual is accounted for as payable or explicitly unpaid. Historical
true-ups are not part of this normal-accrual partition. They are separate payment
adjustments. Skybase's previously unbooked true-ups are recognized as September
Sky expense and reduce Sky Net Revenue and the TMF waterfall. A historical
earning date does not mean the expense was already booked.
No prior-month settlement artifact is changed.

## Additional valuation sanity checks

An internal 0–8% annualized-return screen is a diagnostic, not a publication
rule or an assertion that every allocation must earn a positive return.
Fresh historical RPC reads (Ethereum via MEV Blocker, Plume via its public
RPC) independently reproduced the two notable NAV outliers:

- Grove E22: unchanged 20,201,743.292497372656956881 ACRDX shares. The live
  Chronicle router's NAV fell from 1.02485174998024 to 1.01968198436364,
  reproducing the **104,438.27786893259390777267 USD loss** exactly. Both
  observations were recently updated at their respective month boundaries.
- Spark S12: unchanged 401.044942988323179297 vault shares. Direct historical
  `convertToAssets(balanceOf(ALM))` returned 907.538799718984340323 DAI at
  opening and 1,091.22453113566395393 DAI at closing, matching the pipeline
  within 0.000000000000001 DAI of valuation rounding. The high annualized ratio is on this
  small residual position; it is not used as a forecast.

`valuation-outlier-check.json` records both boundary blocks and exact reads.
Cash distributions on S26/S28 and E21/E38/E42 are not reliable annualized
returns on the receiving wallet's current balance. S24's outlier is below
one cent of revenue; S66 has a 90.0268 USD valuation decline on a roughly
20 million USD position. No financial values were changed by this screen.

## Upstream DR coverage integration

The final close must use the current DR source coverage: Grove's USDS farm,
Skybase's Pendle/Morpho positions and 1inch program, and the additional Osero
programs. The old 28-chunk output is a superseded baseline, not the final
September reward workbook. No old output was force-pushed over the upstream
branch or used to finalize settlements.

Explicit upstream documents establish ownership of 1020/1997/1998/1999 for
Skybase and 3002/3006/3900 for Osero. Other numeric-range assignments must not
be inferred: `osero-codes.md` explains that 3123 is an arbitrary PSM3 field
value, not an Osero program. The remaining unmapped codes await confirmation.

## September DR regression validation

The full unit suite passes: 1,287 tests, with one optional Crypto-dependent
test skipped locally. The 56 focused DR/consolidated tests cover the requested
code ownership, precise September venue accrual, four independent true-ups,
repeat-run idempotence, XLSX/Markdown output, and unchanged historical months.

The review fixes and reproducible close commands are documented in
[`reproduce.md`](reproduce.md). Official SOFR is the default; the historical estimate exception remains explicit and scoped;
the Gelato cash-offset check now matches transaction/log identity.

## Isolated S1/E10 settlement refresh after PR #218

Only Spark S1 and Grove E10 were recalculated. The offline reproduction and
complete before/after audit are in `selective-refresh/README.md` and
`selective-refresh/audit.json`. No full-prime replay or API publication ran.

- S1: 2,113,055.557198851560815424 -> 2,332,672.407089739240730015 USDS
  prime revenue. The additional 219,616.849890887679914591 USDS consists of
  September 14 and 28 treasury receipts. Native yield and borrowing costs
  are unchanged. S2-S5 remain at their saved results by explicit scope.
- E10: 1,065,483.58 -> 733,817.500777 USDS, entirely Sky-direct revenue.
  This includes the prospective 5 bps mark, 12,504.626548 USDS of cash
  realization costs, and restoration of three small capital outflows totaling 2,998.50 USDS.
  Its repriced daily SDE deduction increases borrowing costs by
  516.079599819999221008623 USDS, calculated from saved debt/rate inputs.
- All other venue revenues, four other prime reports, non-MSC and historical
  January-August reports remain unchanged. The finalized DR import is reused.
- Consolidated Sky/TMF are reaggregated from these reports and PR #218's
  September adjustments (Spark +2,392,354.07; Grove -165,013.90).
  Sky net revenue is 14,812,762.21131559140975324007 USDS. Proposed TMF hop
  is 2,661 seconds; vestTot is 116,184,372 SKY. Existing September execution
  data and the 7,372,287.576422652922647064731 SKY burn amount are unchanged.

No October MSC mint/send figures are pinned. The consolidated output remains
a calculated proposal, not an externally reconciled or executed settlement.

## Skybase DR refresh and unbooked historical expense correction (PR #224)

The latest upstream DR snapshot is `ed08241`. September Skybase DR is
105,735.7496915167660193413234 USDS; the four separate historical true-ups
sum to 177,113.780088 USDS. They were omitted from published January-August
reports, so the full amount is now recognized as Sky expense in September.
The earlier treatment that only disclosed these as payments was incorrect.
Earlier API/S1/E10 audit snapshots above are retained as baseline evidence.

Current consolidated Sky net revenue is **14,635,947.43122759140975324007 USDS**.
TMF uses 14,635,947 whole USDS; proposed hop is **2,693 seconds**, vestTot is
**114,797,518 SKY**, and the Core Council Buffer transfer rounds to
**2,927,189 USDS**. Skybase's total payment remains **320,925.535700 USDS**.
See the current Skybase reconciliation and `skybase-dr-refresh-downstream.json`.
