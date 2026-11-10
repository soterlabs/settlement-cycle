# Historical reconciliation inventory from PR #220

Range: PR #220 through the merged #278 main commit recorded in `pr-inventory.json`.
Published September baseline: `536f1492089ed82fbf6eaabb956f7a194b887d47`.
Earning months: January-September 2026. All 55 merged PRs are classified.
The amounts below are booked component totals; credits already settled before
the baseline are excluded and existing October credits are counted once.

- **Aave/SparkLend event boundaries (#220):** exact production replay across 139 allocation-months; Spark -1,089,098.10 income change versus the previous draft, Grove +283.23. Osero +270.64 income is absorbed by its negative supply share and adds no payment.
- **Grove async redemptions (#226):** 78,530.06 net CoF credit, including the JAAA debit and subtraction of the prior 2,508.55 credit; already configured for October.
- **Grove issued async deposits (#226):** 35,889.68 additional CoF credit, marginal to corrected redemptions.
- **Grove Base Steakhouse vault (#264):** 7,219.49 September income; full owner history establishes no earlier 2026 position.
- **Grove Diamond PAU Uniswap (#264):** 1,030.45 income across August/September, including the newly measured 27.90 August amount.
- **Grove small BUIDL exits (#268):** 4,000.18 net Sky-side debit after restoring 4,001 of omitted capital and deducting previously credited cash fees.
- **Grove warehouse principal (#268):** corrected principal schedule/venue CoF attribution; zero separate whole-prime credit.
- **Grove Basin reporting (#268):** removes misleading negative venue PnL presentation; zero additional whole-prime exemption or credit.
- **Spark native RLUSD partner receipts (#267):** 1,373,208 September income, recognized once through the actual recipient asset.
- **Spark Sentora RLUSD S72 (#267):** 3,674.16 September investment income.
- **Spark SparkLend RLUSD S70 (#267):** 2,400.00 September income, including disjoint external spToken receipts; included once in the comprehensive #220 lending replay.
- **Spark native-USDS bridge transit (#274):** 255,869.48 January-September CoF credit; excludes October's 75,063.88 effect.
- **Spark Maple async ownership/capital (#278, implementing #263):** 41,042.54 debit for overstated January-July income; global CoF unchanged.
- **Obex Maple async ownership/capital (#278):** 273,508.90 July income debit; global CoF unchanged.
- **Osero financed-shortfall double deduction (#270):** 5,514 send-only refund for executed July-September payments; already configured for October.

No independent historical payment is added for Osero codes 3014/3017 (#258/#260),
Spark Ethena/BUIDL/USTB/USCC zero-effect findings, Spark's existing JTRSY adapter,
atomic Arkis exits, source/output protection (#266), or documentation/reporting
plumbing. The complete JSON inventory classifies those PRs individually.

Partner rebates received in October are ordinary October income, irrespective
of their commercial reference period. They are not January-September true-ups.
The RLUSD items above belong to Spark; Grove's idle RLUSD was already tracked
in the baseline. Venue-average/CoF reattribution is not a second payment.
