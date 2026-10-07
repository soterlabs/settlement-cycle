# BUIDL redemption cash realization

From September 1, 2026, Grove E10 recognizes the difference between the
carrying value of redeemed shares and the issuer's actual USDC payment on
the cash receipt date. USDC proceeds themselves remain capital in the
receiving cash venue. E10 is 100% Sky Direct Exposure, so the realization
variance flows to Sky and does not change Send to prime.

The August 31 request carried at $1 settles September 1 with a $12,499.846781
loss. Requests dated September 1 onward carry at $0.9995; a later cash receipt
recognizes only the variance from that mark. The existing $15,000
administrative-fee rule and SDE in-flight CoF treatment are separate.

`config/redemption_settlements.yaml` defines two narrow Ethereum Transfer
filters: BUIDL shares from the Grove ALM to its redemption receiver, and USDC
from the verified cash payer to that ALM. A fixed, audited empty-ledger
checkpoint on August 31 starts the history. It never advances with the
report month, so unresolved requests survive weekends and arbitrarily long
settlement delays. The shared incremental log store supplies cached history;
other venues and chains are not scanned by this component.

Exact transaction/log links take precedence. Otherwise an issuer payment
must uniquely match a group of outstanding requests at the expected 99.95%
payout, within the smaller of $5 or 0.1% of the cash amount. This estimate is
only a matching aid: actual cash determines the realized variance. There is
no maximum request age or four-request batch restriction. Previously settled
requests are consumed before the report period, preventing repeat recognition.

Ambiguous matches, material unmatched issuer cash, or unusually complex
batches require an explicit transaction/log link instead of guessing. Small
unmatched issuer dust remains visible in provenance. Partial payments against
one request need explicit allocation support before use; the current matcher
consumes whole requests. Requests for which no cash has arrived remain
outstanding and do not block closing the month. The ledger records both
transaction legs, outstanding carrying values, and the signed revenue
adjustment in daily/monthly provenance and the Markdown report.

The same helper is called by both daily and monthly calculations. No
historical January-August recognition or published artifact is changed.
The September boundary cost is included in E10 revenue. Therefore the
separate historical `sky_adj` remains -165,013.90; increasing it to
-177,513.75 after the E10 update would double-count the September fee.
The historical credit plus this boundary cost is 177,513.75 USDS.

Validation uses the real August 31 / September 1 events, including a separate
post-haircut September 1 payment whose variance is only -0.331189 USDS.
Tests also cover six-week delay, multi-request payouts, ambiguity,
idempotency, cutoff isolation, historical non-recognition, and the SDE split.
The shared production path supports both daily and monthly runs. This PR
refreshes only September settlement venues S1/E10; it does not publish API
revisions. The selective API refresh is a separate proposed feature.

A targeted scan of both event streams through September 30 matches 14 cash
settlements with no outstanding requests or unmatched payments. Total cash
realization is -12,504.626548 USDS: the boundary cost of -12,499.846781 plus
-4.779767 of post-haircut cash variances. The latter are separate September
items, not another 5 bps charge or a January-August true-up. The exact events
are in `docs/september-2026-close/buidl-september-ledger-validation.json`.

The existing $1M capital filter also excluded three verified 1,000-share
redemptions on September 9, 14 and 21. The ledger restores their combined
$2,998.50 carrying value to capital outflows without admitting incoming yield
mints. The cash variances remain separate realization costs; principal is no
longer mistaken for a loss. Provenance lists all three small exits explicitly.
