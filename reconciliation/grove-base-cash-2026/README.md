# Grove Base cash capital and E12 weights

This records the method change and its incremental historical booking impact.

| Month | E19 income change | E23 income change | E43 income change | Total |
|---|---:|---:|---:|---:|
| 2026-01 | 18.22 | 0.00 | 0.00 | 18.22 |
| 2026-02 | 0.00 | 0.00 | 0.00 | 0.00 |
| 2026-03 | 0.00 | 972.71 | 0.00 | 972.71 |
| 2026-04 | 0.00 | 1,894.70 | 0.00 | 1,894.70 |
| 2026-05 | 0.00 | 1,547.75 | 0.00 | 1,547.75 |
| 2026-06 | 649.41 | 3,045.28 | 0.00 | 3,694.68 |
| 2026-07 | 3,055.98 | 943.33 | 0.00 | 3,999.31 |
| 2026-08 | 22.31 | 0.00 | 0.00 | 22.31 |
| 2026-09 | 1,556.20 | -322.92 | 386.37 | 1,619.65 |
| 2026-10 | -3,207.56 | -11,906.66 | 23,179.17 | 8,064.96 |

January-September incremental income: **13,769.34 USDS**. October: **8,064.96 USDS**.
Totals use unrounded components; displayed rows can differ by a cent.

The $7,219.49 E43 missing-venue September credit remains in PR #277. Here only
its additional $386.37 cash-method difference is included, avoiding overlap.

## Historical baseline difference discovered during replay

June E23 already differs by **-10,747.77 USDS** between the preceding production
method on pinned chain inputs and the frozen published row (514,393.32 published
versus 503,645.55 replayed before this change). Its frozen opening value is
103,110,534.95, while the pinned on-chain opening value is 100,110,790.82.
This inherited source/boundary difference is separate from the cash-method
increment and is retained explicitly in `prior_baseline_variances`. It must be
checked against the earlier published reconciliation baseline before booking.
The June published row is unchanged between the earlier reconciliation baseline
`f5fa37e` and the last MSC baseline `536f149`. Its difference is included once
in the Grove-only replay. After combining it with the cash-method increment,
the amount to add to the existing #277 ledger is **3,021.57 USDS**:
13,769.34 - 10,747.77. The earlier E43 missing-venue credit stays counted once.

## E12 reporting-only correction

| Month | Measured average | Previous CoF weight | Corrected CoF weight |
|---|---:|---:|---:|
| 2026-08 | 26,605,619.71 | 26,605,619.71 | 26,605,619.71 |
| 2026-09 | 17,786,194.41 | 25,000,000.00 | 17,786,194.41 |
| 2026-10 | 8,387,500.28 | 25,000,000.00 | 8,387,500.28 |

The January-August effective weights are unchanged. The preallocation floor
ends September 13, when the verified withdrawal took the actual position below
25M. E12 changes venue CoF attribution and yields, with zero change to global
CoF, Sky revenue or the agent settlement. Diamond PAU E44 remains separate.

## Reproduction and limits

```sh
SETTLE_NO_CACHE=1 python scripts/reconcile_grove_base_cash.py --output /tmp/grove-base-cash-results.json
```

The replay blocks network access and uses the same production venue-revenue
formula for both methods. `inputs.json` retains complete bounded owner cash,
share and ERC4626 histories plus pinned balances, daily quotes and headers.
`e12-withdrawal.json` retains the NFPM withdrawal and pre/post RPC witnesses.
The new cash source checks gross asset/share legs per transaction and exact
opening-to-closing share conservation, rejecting unexplained direct share
transfers and nonzero withdrawals paid to another receiver. Source failures
propagate; they cannot become synthetic month-end capital or empty histories.
NAV-based endpoint valuation and the existing share/NAV exposure averages remain
unchanged. Exact cash feeds the existing `erc4626_period_inflow` override; the
verified raw share movements separately feed average-value reporting. The replay
requires zero average-value difference in every one of the 30 venue-months.
The three Base vaults are
explicit opt-ins; other primes and async-vault policies are unchanged.

No frozen settlement, live adjustment entry, database revision or payment is
changed by generating this comparison. October income belongs in ordinary
October MSC; historical corrections require the combined reconciliation netting.
