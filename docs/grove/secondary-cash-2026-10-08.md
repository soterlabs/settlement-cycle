# Grove PAU cash was traced but omitted from allocation totals

Grove's [July 2 spell](https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20260702/GroveEthereum_20260702.sol)
initializes the additional PAU with its own ALLOCATOR-GROVE-A. The capital
normalizer already follows cash at that holder, including the August 19 draw,
swaps and NFT deposit/withdrawal tests. However, only its NFT had an allocation
row. Temporary AUSD and USDC holdings were absent from the summed allocation
costs despite remaining in the replay ledger.

The Apollo fix narrowed uncertainty enough to expose a $90.92 mismatch on
August 19. Mapping the actual PAU cash accounts resolves that omission.

The new helper adds tracing-only rows for observed AUSD/USDC/DAI/USDS balances
at the explicitly configured PAU address. Only AUSD and USDC have nonzero
observations in this replay. Already mapped cash is not included twice, and
unrelated holders are not assigned. All underlying events, debt, funding
origins and exemption flags remain unchanged.

| New tracing row | Average borrowed principal | August CoF |
|---|---:|---:|
| E14_PAU_CASH (AUSD) | $15,341.46 | $47.40534271 |
| E15_PAU_CASH (USDC) | $15,308.34 | $47.30300632 |

These rows are **traced**, adding **$94.70834903** to the eligible subtotal:
**$1,233.51 → $1,328.22**. Their revenue and APYs remain unavailable because
this diagnostic does not create a revenue snapshot for the additional holder.
The extra eligible cost is not identical to the former $90.92 bound gap: the
joint upper bound is capped by total debt, rather than summing venue maxima.

GROVE-A's interval is now **$10,034.90–$11,784.74**, containing its unchanged
$11,784.74 control. Neither ilk fully reconciles. The 36 unmatched receipts
and 256 unmatched outflows remain unchanged; this was an allocation ownership
omission, not a newly discovered cash transfer.

Evidence: `reconciliation/grove_secondary_cash_2026_08.json`. All 313 relevant
tests pass, including a two-day draw/earnings/reinvestment regression where
secondary cash plus investment borrowing costs equal the unchanged ilk total.
No published report, API revenue, total debt or global CoF is modified.
