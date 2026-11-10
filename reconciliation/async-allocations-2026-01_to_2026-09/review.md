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

## Round 2

Pending review of the first fix commit and complete CI; merge is not authorized
by this record until the requested second round and passing checks are complete.
