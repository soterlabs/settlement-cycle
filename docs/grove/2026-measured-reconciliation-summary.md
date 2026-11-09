# Grove: measured 2026 reconciliation amounts

This is **not a completed January–December audit**. Available identified
corrections mainly cover January–September; the missing venue income below
has only been measured for September. Unknown periods and receipts are not
zero. The numbers describe changes to Grove's MSC balance, not every venue's
redistributed accounting CoF. Positive benefits Grove; negative is a debit.

| Venue | CoF already configured in the October bridge | Further measured change, not booked |
|---|---:|---:|
| JTRSY (E9) | +68,310.04 | +35,889.68 |
| BUIDL (E10) | +43,637.97 | −4,000.18 |
| JAAA (E8) | −33,417.95 | 0.00 |
| Base Grove x Steakhouse USDC (E43) | 0.00 | +7,219.49 |
| Diamond PAU Uniswap (E44) | 0.00 | +1,002.55 |
| Warehouse principal (E42) | 0.00 | 0.00 — redistributes existing CoF |
| Basin reporting (E41) | 0.00 | 0.00 — redistributes existing CoF |
| **Measured totals** | **+78,530.06** | **+40,111.54** |

The further-measured total is not a new approved MSC instruction. It mixes
CoF relief, a Sky-share debit and additional investment revenue, which must
use their existing separate settlement mechanisms. Neither configured inputs
nor a replay establishes transaction execution or rules out off-report credits.

## Basis and exclusions

- JTRSY's further 35,889.68 is the issued-deposit CoF replay for January,
  March and April, marginal to the already configured redemption corrections.
- BUIDL's further debit reverses 4,001 of artificial small-share principal
  losses in published May/August income, net of 0.821229 remaining cash cost.
  See [the complete explanation](remaining-reconciliation-items.md). It must
  not also be added from another income backfill.
- JAAA's settlement-day correction is already part of the configured bridge.
- The Base and PAU income measurements are September only. In particular the
  PAU position already had a September opening balance; earlier income is
  not yet quantified. These are not full-year venue totals.
- Warehouse/Basin changes affect the allocation of CoF between venue rows,
  not Grove's aggregate borrowing charge. Their per-venue PnL redistribution
  still requires a fresh calculation; the zeros above are headline MSC effects.
- The earlier **165,013.90** September BUIDL fee/CoF credit is excluded from
  these additional amounts. The approved redemption replay already subtracts
  its 2,508.55 CoF component.
- Partner rebates are separately configured in October and excluded here.
  They include 2025 earnings paid in 2026; calling their entire cash amount
  2026-earned income would be misleading. Robinhood receipts and unreviewed
  later months are not assigned an assumed zero.

The per-venue split of configured CoF reproduces the existing 78,530.06 net
without changing that instruction. The only joint JTRSY/BUIDL days are May
18–19: both original and corrected utilized balances exceed the historical
1B subsidy cap, so all component principal changes have the same marginal
rate. The summary refuses to split a joint correction crossing the cap or the
zero-balance floor. Every venue delta must reproduce its principal change,
and every credit must reproduce the recorded charge difference; offsetting
venue errors cannot pass merely because their grand total is unchanged.

Replay without network access:

```sh
python scripts/summarize_grove_2026_reconciliation.py
```

[Machine-readable partial summary](../../reconciliation/grove_2026_measured_reconciliation_summary.json).
The aggregate 118,641.60 includes the 78,530.06 already configured; it must
not be booked in addition to that existing bridge.
