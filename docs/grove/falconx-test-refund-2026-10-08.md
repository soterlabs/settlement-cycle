# FalconX onboarding-test cash returned before the production deposit

On November 24 Grove sent **1,000 USDC** to the configured FalconX deposit
wallet (`0xd94f…8036`). The wallet forwarded exactly 1,000 USDC to
`0x1157…4101` six minutes later. That address then paid **10.002600** and
**989.997399 USDC** back to Grove's Ethereum ALM, before the $25m production
payment. These receipts total **999.999999 USDC**, returning the test principal.

The existing E36 boundary recognized AUSD returns from its configured anchor,
but missed these two USDC refunds. The correction is limited to these exact
receipts, supported by the test funding/payment sequence. It does not classify
arbitrary cash from the commingled `0x1157…4101` wallet or trace its other funds.
All four canonical transfers are retained in
`tests/fixtures/grove_falconx_test_refund_events.json`.

Later E36 principal caps are recalculated: otherwise the same refunded principal
would be released again when AUSD comes back. Under the existing return-cap
convention, aggregate excess becomes **120,746.468784**, versus the prior
119,746.468785 that omitted the refund. This is capital-tracing classification
only; published revenue and settlement reports are unchanged. The claim still
closes in April, with zero August E36 principal.

August replay removes two more receipt gaps (**16 → 14**), retaining 239
unmatched outflows. Eligible costs remain **$11,784.59**, and BLOOM-A is still
unresolved. Tests cover recycling the refund into the next deposit, preventing
double principal release, cutoff after one receipt and contradictory evidence.

Evidence: `reconciliation/grove_falconx_test_refund_2026_08.json`.
