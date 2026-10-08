# Small BUIDL receipts preceding their main redemption payouts

Six issuer cash receipts in August, September and December 2025 were left as
unexplained receipts. Each precedes a much larger payment from the same configured
BUIDL payer, after a single outstanding redemption request. The full request
inventory contains exactly these six requests through December 16; previous
claims had settled before the next request. Combined payments match the request
less its 5bp charge within $5. The six advances total **$4.540517**.

The event stream has no explicit payment-to-request identifier. These links are
reviewed associations supported by the unique outstanding claim, exact issuer
and recipient, chronology, and expected aggregate proceeds. They are not a
sender-wide gift rule or a cryptographic cross-chain message proof.

The capital adapter now releases the actual small receipt from that claim when
paid. The larger payment closes the remainder, retaining the actual aggregate
exit shortfall. A cutoff after the advance retains the unpaid claim; it neither
uses the later cash value nor fetches a future payout. Raw events and transaction
IDs are retained in `tests/fixtures/grove_buidl_partial_payment_events.json` and
`compute/grove_buidl_partial_payments.py`. No past revenue reports are restated.

August diagnostic replay removes **six receipt gaps (22 → 16)**. Outflows remain
239 and eligible borrowing costs remain **$11,784.59**. The next oldest unknown
receipts are the two November 24 USDC payments totaling $999.999999; later
uncertain repayments still prevent BLOOM-A from qualifying. Removing an early
unknown receipt does not automatically prove every later funding route.

Evidence: `reconciliation/grove_buidl_partial_payments_2026_08.json`.
