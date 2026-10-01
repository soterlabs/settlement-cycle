# Isolated September settlement refresh

Only Spark S1 (spUSDS) and Grove E10 (BUIDL) are recomputed. No API revisions
are published and no full-prime pipeline runs. Other venue rows are reused
exactly. All January-August report artifacts and the four other primes'
September reports remain untouched.

## Reproduction

```sh
PYTHONPATH=src python scripts/refresh_september_selected_venues.py \
  --output /tmp/september-selected-refresh
```

This is an offline, fixed-scope script, not the proposed general API feature.
Use `--write-reports` to write only the two September prime reports. It imports
the existing finalized DR workbook/CSV without an independent DR replay.

The script checks immutable baseline file hashes and reproduces the original
E10 row exactly before changing its inputs. Its explicit schema migration
adds only empty redemption metadata to these two pinned snapshots. Production
schema/version guards are unchanged. Repeating the script starts from the same
baselines and produces identical calculations, rather than adding a correction
to previously corrected output.

## Source collection and reuse

Both baseline calculations were made at code `c32f25a`, independently checked
against September 30 API output, and updated to official September 30 SOFR
(3.90%). Their complete saved results and rate provenance are included here.
Their API-equivalent result hashes are recorded in `audit.json`.

Newly collected inputs use the same Ethereum closing block **26093737**:

- `spark-s1-treasury-events.json`: `window_logs` for spUSDS Transfer events,
  from the two configured reserve treasuries to Spark ALM, September 1-30.
  Two receipts, September 14 and 28, total 219,616.849890887679914591 USDS.
  The source records transaction/log identities. Existing native aToken yield,
  endpoints, capital flows and idle deductions are reused.
- `grove-e10-capital-flows.json`: `HyperSyncBalanceSource` for BUIDL only,
  Grove ALM holder, the prime's existing start date and $1M capital filter.
  Its 51 daily capital rows reproduce the old E10 aggregate and time-weighted
  value. No other venue balance or NAV is queried.
- `../buidl-september-ledger-validation.json`: two filtered Transfer streams,
  BUIDL exits to the request receiver and USDC receipts from the issuer payer,
  August 31-September 30. Its 14 matches are rebuilt by the production matcher
  offline, including the separate August 31 test payment from the boundary
  fixture. The audited closing ledger has no outstanding or unmatched cash.

E10 uses its saved $1 daily token balances with the current dated $0.9995
mark. The August 31 opening value remains unchanged. No in-flight SDE window
is active within September; the existing August window is preserved. Only
E10's daily SDE delta changes the saved aggregate deductions. The production
interest function first reproduces baseline borrowing costs within the
existing 1e-6 USD serialization tolerance, then recalculates costs using that
delta. It reuses the exact saved debt, SSR/SOFR, ALM/PSM/Curve/lending/Basin
inputs. No unrelated multichain reads or new reference-rate queries occur.

## Results

| September revenue | Before | After | Change |
|---|---:|---:|---:|
| Spark S1, prime revenue | 2,113,055.557199 | 2,332,672.407090 | +219,616.849891 |
| Grove E10, Sky-direct revenue | 1,065,483.580000 | 733,817.500777 | -331,666.079223 |

E10's change consists of -321,627.210885 from first applying the haircut to
the opening position, -532.741790 from remaining September NAV/flow repricing,
-12,504.626548 cash realization, and +2,998.50 restoring three verified small
exits to capital instead of loss. The opening markdown is a one-time September
policy-transition effect on the entire remaining position, not a collapse in
interest earnings and not a regeneration of earlier months. The new daily SDE valuation separately increases
Grove borrowing costs by 516.079599819999221008623 USDS. Spark borrowing costs
are unchanged. These are accounting amounts, not a new fee charged on gains.

`audit.json` identifies every preserved venue and records before/after rows,
complete-result hashes, selected configuration/implementation/input hashes,
and dependent aggregate deltas. Baseline code/config provenance remains distinct
from the selectively recalculated inputs in the generated reports.

## Dependent reports

Sky total and TMF are reaggregated from the updated two prime reports and the
saved four other primes/non-MSC artifacts. Existing PR #218 September adjustments
are applied: Spark `sv_adj: 2392354.07`, Grove `sky_adj: -165013.90`.
The September boundary realization is already in E10, so it is not added again
to the Grove historical adjustment. The four Skybase payment corrections stay
separate and unchanged.

`downstream-audit.json` records the consolidated result and TMF calculations.
The same saved September activity/state/pins, TWAP and backstop inputs are
reused. Only the new Sky net revenue feeds the waterfall. Sky net revenue is
14,812,762.21131559140975324007 USDS; proposed hop is 2,661 seconds and vestTot
is 116,184,372 SKY. Whole-USDS mint/send rounding explains why the consolidated
delta differs from summing unrounded venue amounts.

The output is a calculated settlement proposal. Adding adjustment-only entries
to `msc_preview` does not constitute published mint/send pins; the report keeps
that distinction visible. API refresh remains a follow-up described in
[`../../PRD_selective_venue_revenue_refresh.md`](../../PRD_selective_venue_revenue_refresh.md).
