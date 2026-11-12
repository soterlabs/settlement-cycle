# Grove adjustments — October 2026 earning cycle

Updated 12 November 2026. No settlement transaction is asserted as executed.
Historical corrections and partner rebates are booked once in October;
published January–September reports are not rewritten.

| Item | Sky adjustment (USDS) | Grove supply adjustment (USDS) |
|---|---:|---:|
| Jan–September reconciliation, net of previously booked credits | -110,419.56 | 11,554.74 |
| partner rebate — JAAA 2025 | 58,578.87 | 354,741.13 |
| partner rebate — JAAA January–July 2026 | 117,303.63 | 455,972.37 |
| partner rebate — JTRSY 2025 | 356,999.00 | 0.00 |
| partner rebate — JTRSY January–July 2026 | 713,408.00 | 0.00 |
| **Total MSC adjustments** | **1,135,869.94** | **822,268.24** |

Partner rebates total **2,057,003.00 USDS**, matching six USDC receipts on
October 20–21: **1,246,289.50 Sky / 810,713.50 Grove**. The calculation preserves
invoice nets and allocates them using saved on-chain daily exposure weights.
JTRSY is entirely Sky under the approved historical SDE-like interpretation,
including Spark custody. JAAA uses the provisional $325m Ethereum cap from
20 November 2025 through 8 March 2026. Proportional fee allocation is an explicit
assumption. No fee is deducted a second time.

This replaces the earlier statement-based split, including its $360 mismatch;
it is not an additional rebate. The ordinary October venue revenue is unchanged.
The resulting October instructions are **mint 8,866,792 / send 2,170,826 USDS**.
Gross mint is unchanged from the previous PR version; unrounded Grove payment
falls by **105,143.70 USDS**, increasing Sky's net by the same amount before
whole-token rounding. The actual whole-token send decreases by 105,143 USDS.

Reproduce with `python scripts/calculate_october_partner_rebate.py`.
Inputs, full precision weights, receipts and assumptions are committed under
`reconciliation/2026-10/partner-rebate-*`, `partner_rebates.json`, and
`PARTNER_REBATE_RECIPE.md`. Historical corrections come independently from
`reconciliation/2026-01_to_2026-09/data.json`. Replay applies replacement totals,
so repeating it cannot add the historical credits or partner receipts twice.

The rebate allocation does not independently prove that no historical NAV
included it; no prior rebate recognition was identified in the retained review.
