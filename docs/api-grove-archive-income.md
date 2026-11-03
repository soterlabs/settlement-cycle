# Grove archive income — monthly historical import

`GET /v3/reporting/statements?prime=grove&year=2026` defaults to
`prime-management.archive-income.v3.5`. It adds 22 monthly statement inputs and
12 non-additive Merkl reference figures from the October30 archive. It also
retains the existing GACLO/Warehouse schedule and all older report policies.

| Stream | Published history | Statement treatment |
| --- | --- | --- |
| Monad AUSD Morpho vault | January–April | Override the existing `investment.morpho.grove_monad_ausd` leaf with net share-value return after deposits/withdrawals; AUSD at USD par |
| Agora | January–September | `rewards.issuer.agora`: supplied E38 receipts, including explicit zero-receipt months; no make-whole accrual or receivable inferred |
| USDG | August–September | `rewards.issuer.usdg`: nominal USDG rewards received on their source chain at USD par; no claim about earning periods or Ethereum settlement |
| Plume partnership | April | `rewards.partnership.plume`: supplied USD partnership cash, under other incentives/rewards |
| PLUME disposal | June | `gains.plume_disposal`: supplied USD proceeds under the archive's accepted zero-cost-basis treatment; noninterest disposal gain, not a second partnership payment |
| Merkl Core/Horizon | Five positive position/month groups: February, April, July | Override the corresponding existing `rewards.lending.aave.*_rlusd` leaves with verified scoped in-kind distributions, using the supplied unit-price=$1 comparator |

Receipt recognition follows the existing lending/issuer policy; it does not
assert that rewards were economically earned in the month received. Earlier
entitlements may settle later. Merkl in-kind distributions are **not bank cash**.
Separate interest mints in the receipt transactions are excluded. All 6 source
claim events tie to independent coverage and current claimed state in the supplied
scope; universal completeness is not claimed. Only known positive amounts are
imported, as partial metrics. Unobserved months are not certified zero.

AUSD/USDG par and Merkl token unit-price conversions are estimates of USD value,
not independently verified fair values. They carry `quality: estimate`, including
through signed subtotals/YTD. Native quantities and the conversion basis remain
explicit. Separate fees and other operating expenses remain unknown; vault returns
include costs embedded in share value. No new principal, claim receivable or
income received in advance is invented.

## Reading the additional schedule

`archive_income[]` contains `id`, `line_id`, `label`, `month`, `metric` and
`included_in_totals`. A metric uses the existing value/known-value/status contract,
plus `source_currency`, `conversion`, `quality`, `period_start`, `period_end` and
`source_ref`.

- `included_in_totals: true` means this metric is the **same metric** as the named
  statement leaf, never an extra amount. Existing overlapping leaves are replaced,
  not added. There are 22 such rows.
- `included_in_totals: false` rows have no `line_id` and are **reference only**.
  These comprise 9 nonzero recorded Merkl cumulative changes and the coherent
  April–June Horizon earned-income scenario. Neither enters statement totals.
- Merkl's scenario uses the supplied daily reward/TVL × same-day EOD holding
  allocation. All three months are shown together. It must not be added to claims,
  to recorded changes or to June's aggregated catch-up. Historical recipient
  economic-cutoff leaves remain missing. Scenario spread is not a confidence bound.
- Monad rows additionally expose `balances` (`opening_value`, `closing_value`,
  `deposits`, `withdrawals`). The return reconciles as closing − opening − deposits
  + withdrawals. These balances/capital movements are not extra income and are
  not added to entity NAV or portfolio totals.

The dashboard keeps this schedule collapsed under **Archive income**. Its CSV
retains inclusion, receipt/return/scenario basis, quality, native currency,
conversion, balances and source selection. Main-statement CSV contains only
statement rows, not the reference scenarios.

## Reproducibility and updates

The same twelve `offchain_revisions` slots pin combined monthly archive sources
in v3.5; no extra unbounded revision array is introduced. Each combined source
pins its older GACLO/Warehouse base revision, original CSV hashes, archive hash,
canonical snapshot hash and scope limitations. Old snapshots and v3.4 selections
remain replayable unchanged. Use `&revision=<selection>` for immutable exports.

Only the reviewed CSVs are bundled. Passwords, credentials, unrelated files and
the full archive are excluded. Publication is a monthly read projection: deploying
the bundle backfills the supplied history without daily revenue processing,
provider calls, migrations or MSC settlement changes. Later months require new
source schedules; no extrapolation or live forecast is introduced. Agora's unknown
contractual offsets, FalconX custody gaps and early LP history are not repaired
by this import.
