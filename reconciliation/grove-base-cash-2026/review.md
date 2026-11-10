# PR review and fix

## Fixed: cash income must not change the existing exposure-average convention

The initial implementation reused cash capital as the average-value series.
That changed all three Base vaults' reported weighted averages as a side effect
of correcting their revenue, although their prior share/NAV-based averages had
already been verified. The review separates these two inputs through the
existing `VenueRevenueInputs.erc4626_period_inflow` override.

Exact verified cash remains the period capital subtracted from endpoint value
changes. The same verified share mints/burns are independently repriced under
the existing daily-NAV convention solely for the average-value series. No
second share-event scan or synthetic month-end correction is needed.

A regression exercises a 100 cash deposit whose shares are worth 110 at the
close: income is 10 while the established exposure average stays 110. The
monthly route test checks this separation and unchanged Sky borrowing revenue.
The retained replay enforces unchanged averages across all 30 venue-months.
January-September and October income changes remain 13,769.34 and 8,064.96.

## Other reviewed controls

- E12's January-August weights remain unchanged; its September and October
  weights use measured values after retiring the dated floor.
- All gross share and cash legs must reconcile per transaction. Provider or
  coverage failures cannot become zero streams or synthetic capital.
- Same-day deposit/withdrawal round trips retain actual cash income.
- Only E19/E23/E43 opt into the new cash method; async and other-prime policies
  keep their existing routing.
- E43's already-calculated missing-venue credit and the separate inherited June
  E23 baseline variance remain outside this method's incremental booking total.

No payment, frozen report or production adjustment is changed by this review.
