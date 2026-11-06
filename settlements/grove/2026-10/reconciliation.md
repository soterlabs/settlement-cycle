# Grove adjustments — October 2026 earning cycle

Operator-approved November 6, 2026. These adjustments accompany the October
earning cycle, normally settled in November. This record does not assert that
the settlement transaction has executed.

| Item | Sky share adjustment (USDS) | Grove supply-side adjustment (USDS) |
|---|---:|---:|
| Historical in-flight CoF correction | -78,530.06 | 0.00 |
| Partner rebates — allocation to Sky | 1,141,145.80 | 0.00 |
| Partner rebates — allocation to Grove | 0.00 | 915,857.20 |
| **Total MSC adjustments** | **1,062,615.74** | **915,857.20** |

The partner allocations sum to 2,057,003.00 USDS, matching the six receipts
on October 20–21. They use the statement's detailed figures. The operator
accepts the 360.00 USDS difference in the statement's final beneficiary totals
as an error; this is arithmetic validation, not an independently reconstructed
time-weighted rebate entitlement. Payer identities are labeled **partner**.

The existing `msc_preview` mechanism applies `sky_adj: 1062615.74` and
`sv_adj: 915857.20`, before final whole-USDS rounding. The CoF component
reduces debt minted; it does not create another direct payment to Grove.
Partner supply-side revenue uses the normal mint/send calculation. Final
transaction amounts also depend on the ordinary October results.

The CoF amount includes the JAAA settlement-day debit and subtracts the
2,508.55 USDS CoF credit already recorded in September. The earlier
165,013.90 USDS combined fee/CoF correction stays in September and is not
repeated here. No other overlapping credit has been identified in the
records reviewed. The rebate allocation does not establish that the amount
was excluded from prior NAV/income; no prior rebate recognition was identified
in this investigation.

Evidence: `reconciliation/2026-10/sde_inflight_cof.json`,
`reconciliation/2026-10/partner_rebates.json`, and
`docs/grove/sde_inflight.md`.

The deposit-side extension separately calculates a further 35,889.68 USDS
of CoF relief for January, March and April 2026, marginal to the corrections
above. See `reconciliation/2026-10/async_deposit_cof.json`. It is not included
in this accepted settlement bridge: it remains a calculated backfill pending
comparison with prior settlement credits. Combining the separately rounded
redemption and deposit lines would give 114,419.74 USDS of total CoF relief.
