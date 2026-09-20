# Allocation financing validation — PR #215

Development results as of 2026-09-20. Existing published settlement artifacts
were read, not regenerated. These results do not establish complete production
coverage or a successful reconciliation for every ilk.

## Production wiring and NFT coverage

The monthly CLI, monthly runner scripts and daily worker request allocation
analytics. JSON provenance includes gross/net APY; the monthly summary and
workbook include an allocation-yield table. Daily APY annualizes the month-to-date
period, not the last day's revenue alone. This code is on the PR branch;
production deployment depends on merging/releasing it.

V3 funding reads historical NFT transfers and liquidity events, tracks principal
between DecreaseLiquidity and a later Collect, and classifies excess collected
fees as own funds. NFT holder transfers preserve funding basis. V4 funding reads
pool/PositionManager-scoped ModifyLiquidity events and configured NFT ids. V4
fee-settlement differences still remain unmatched until authenticated; they are
not presumed borrowing. Existing daily V4 pool snapshots supply idle fractions.

If funding extraction fails, settlement publication continues with unavailable
net analytics. Gross yield remains available where the revenue/exposure pair is
valid. Undefined values remain null, including pass-through loan escrows whose
cash balances do not measure the outstanding loan. No APY-range warning or
publication gate was added.

## August reconciliation, before any redistribution

| Ilk | Sum of allocation costs | Existing borrowing charge | Allocation sum minus charge |
|---|---:|---:|---:|
| ALLOCATOR-OBEX-A | 1,195,814.76 | 1,248,716.85 | -52,902.10 |
| ALLOCATOR-PRYSM-A (Osero) | 7,007.52 | 7,005.67 | +1.85 |

Neither passes a one-cent equality check. A separate financing adjustment is
not counted as a successful allocation reconciliation.

OBEX has no unmatched funding movements. Its net historical Vat frob draws are
384,224,981 USD and its grab debt is 18,306,759 USD through August. The venue's
borrowed basis is 384,224,980.60 USD. Independently applying each existing day's
rate to utilized debt minus the traced venue principal reproduces the
52,902.10 USD difference. Treatment of that additional financing cost is the
open **QUESTIONS.md B19** decision.

Osero uses the exact daily lending-idle fractions. Its direct allocation charge
exceeds the existing total by 1.85 USD; global idle deductions and borrowed-basis
deductions are different quantities. A separate unmatched 1.078572 USD USDC
receipt remains in its evidence; it does not acquire loan basis. Osero's August
gross APY is 1.8158%, and direct-cost net APY is -0.4687%. OBEX's are 4.8818% and
1.2834% respectively.

Grove's five-chain history contains 2,259 transactions. NFT funding support
reduces unmatched receipt/outflow counts from 572/545 to 559/528. Historical
Centrifuge issuances, bridging, off-chain loans and relay custody remain
unresolved. Spark's full-history audit has not completed. Grove has multiple
ilks: an aggregate prime result is explicitly labelled combined-ilk scope and
must not be presented as independent per-ilk reconciliation.

## Temporary gross-APY range audit

Audited 285 allocation-month rows in existing June–August 2026 artifacts across
Spark, Grove, OBEX and Osero. Eighty have undefined APY (zero exposure or a
nonpositive return factor); sixteen are outside 0–8%.

| Signal | Finding / treatment |
|---|---|
| Spark S23, June/August | A pass-through escrow's leftover cash is an invalid loan exposure denominator. The analytics now withhold that APY until beneficial loan exposure is known. |
| Grove E15, August | Approximately -5.89e-27 APY: Decimal rounding, not an economic loss. |
| Spark S12, June–August; S13, August | Very high APY on small Morpho positions; underlying revenue/exposure needs investigation. |
| Spark S61/S62, July; S62, August; Grove E12, August | Negative LP returns. Range alone does not distinguish fees, execution losses or revenue-method errors. |
| Grove E1, July | Approximately 31.90% APY; investigate revenue timing versus time-weighted exposure. |
| Grove E21, June–August | Approximately 9.94–12.05%; off-chain revenue/exposure alignment needs investigation. |
| Spark S28, July | Approximately 8.08%; above the heuristic bound, not proof of an error. |

The range audit exposed an invalid denominator and prompted the escrow safeguard.
It did not validate every existing venue revenue figure. None of these
historical settlement amounts were restated or clamped to the heuristic range.
