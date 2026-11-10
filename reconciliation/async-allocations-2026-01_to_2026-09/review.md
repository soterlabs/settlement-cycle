# Implementation review record

## Round 1 — initial implementation `22fdb19`

Reviewed shared exposure, source completeness, units, exact pins, capital/revenue
composition, historical evidence, snapshot/report wiring and cache persistence.

Findings addressed in the first fix commit:

- Historical Maple queue request IDs used a different ABI. Retain both exact event signatures; reject invalid 128-bit IDs and reconcile event history to pinned owner state.
- Cancellation proof must belong to this owner's actual outstanding request and match the returned quantity. Validate request IDs/remaining shares; distinguish manual redemption returns from cancellations.
- Manual processing must not remove an unpaid share claim. Add partial manual processing/payment/cancellation regression and canonical USDT refund/Obex owner tests.
- A zero pending claim does not imply an empty wallet. Preserve Ethena's ordinary ~81 sUSDe wallet position, capture its NAVs, and report its unchanged income separately from average-holding changes.
- Retained derived quotes and metadata need validation. Verify response/input hashes, replay daily NAV reads, verify query coverage and reproduce published Maple income/averages to sub-micro-dollar tolerance.
- Postgres fresh-worker acceptance fixtures provided invalid generic ownership metadata. Extend the raw HTTP fixture with valid protocol metadata and zero owner claims; keep production validation and cache-reuse assertions intact.
- Improve source provenance for payments/cancellation returns and show wallet/pending/economic holdings in the report tab.

Validation: 71 focused tests pass, including Grove's existing cash matching,
canonical Maple histories, Ethena earlier-history conservation, exact issuer
request/receipt pins, partial BUIDL payouts, and monthly orchestration. Initial
full CI passed 2,842 unit tests; its two Postgres failures exposed the synthetic
transport metadata gap above and are being rerun after the fix.

## Round 2 — first fixed implementation `b93e709`

Reviewed the fixed code and repeated the economic/source checks. New findings
addressed in the second fix commit:

- Grouped transactions were sorted by hash within a block. Process them by
  canonical log order; a same-block reversed-hash regression now proves it.
- A transaction may process and cancel part of the same request. Separate its
  processing mutation from its cancellation mutation using canonical log IDs.
- Cash-only settlement must not depend on a later share NAV. Read a share
  quote only when reclassifying shares; fixed underlying payouts work without it.
- Explicitly test that the small BUIDL exit restores omitted capital and realizes
  only the actual cash shortfall, with no duplicate fee or principal income.
- Verify the wallet/pending/economic workbook round trip and retain the full
  historical replay plus per-venue attribution as reviewable artifacts.

Validation: 74 focused tests pass after the second fixes. The first fixed head
passed both full unit and Postgres/cache CI. Final-head CI must also pass before
merge; GitHub's checks on the merged head are the authoritative final result.
The read-only replay reproduces published Maple results and isolates the signed
income deltas in `results.json`, without changing historical rates or reports.
