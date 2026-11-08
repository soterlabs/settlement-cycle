# Validate historical RPC cutoffs before allocation pricing

September 2026 Spark allocation validation found three legacy cached Ethereum
lookups for 23:59:59.999999 UTC that instead pointed to an earlier head:

| Requested day | Cached block | Actual block timestamp (UTC) |
| --- | ---: | --- |
| 2026-09-01 | 25879436 | 2026-09-01 02:26:59 |
| 2026-09-02 | 25886373 | 2026-09-02 01:39:23 |
| 2026-09-07 | 25924309 | 2026-09-07 08:33:23 |

A timestamp less than the requested cutoff is insufficient. The resolver now
requires `timestamp(block) <= cutoff < timestamp(block + 1)`. A stale candidate
triggers an RPC binary search and the same check on its replacement. Missing
block evidence fails closed. Verified results are reused within the resolver
instance so multiple allocations do not repeat the repair search.

For September 1, the stale snapshot gave $441,886,778.2598497647941130082 in
combined pool/lending idle deductions. Re-reading the correct boundary gave
$387,809,077.6884697292251014049, exactly matching the existing settlement
control. No balancing adjustment or scaling was applied.

This change does not overwrite historical settlement reports or publish API
results. Existing saved per-venue deductions must still pass
`validate_idle_control` before pricing. Re-read affected dates and retain the
original input alongside the corrected evidence. Completed transaction-history
capture can be reused: repairing these daily valuation cutoffs does not require
fetching all historical transfers again.
