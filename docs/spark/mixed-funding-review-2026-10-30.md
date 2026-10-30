# Spark mixed-funding replay review — October 30

PR #236 remains research-only. The completed replay covers 28 configured
allocations and six custody rows; it does not validate all of their funding
splits. Twenty-six configured allocations have daily capital differences, and
all 28 retain uncertainty on at least one day. These flags are not themselves
proof of missing transfers.

## Confirmed implementation mismatch: Anchorage returned principal

The capital control explicitly applies the accepted `all-principal` treatment
using `spark_anchorage_scenarios.apply_anchorage_scenario`. The mixed-funding
replay loaded the outstanding-loan yield denominator but omitted that capital
transformation. These are independent requirements.

The affected returns are:

| Date | Transaction | Principal under the accepted policy |
|---|---|---:|
| July 16, 2026 | `0xe74aeeb85c45d4ee617fb2c422f5ed36c4887202e2074194f0926af53cb1a1d6` | 10,036,438 USDC |
| August 17, 2026 | `0x5b719b6e1f8d7ae0035edbe4d4832efa544a3d79474f86c2fbea5899fc4a67fe` | 51,267,944 USDC |

The total is **61,304,382 USDC**, not an estimated settlement adjustment.
The existing July 21 round-trip correction is separate and was already applied.

Without the accepted return treatment, the replay leaves the facility claim
outstanding and treats the returned cash as unknown zero-basis receipts.
Reinvestment then carries too little original funding into downstream venues.
The fix prepares the same accepted history before replay and versions snapshot
identity to reject old results. Global debt and published revenue are unchanged.
The scenario module's historical name does not reopen the already accepted
operator decision.

## Measured control comparison

Recomputing the independent capital control with the accepted treatment
reproduces **all 34 pending rows exactly, on every August day**. This rules out
stale capital-control arithmetic for these rows; it does not validate the
funding split.

Compared with omitting the return treatment, the accepted control lowers
Anchorage's average August original principal by **$34,839,619.88**. Other
allocations change by less than $4 each under this particular control, because
that method already assumes unmatched principal receipts are capital. The
funding replay instead treats them as unknown, so its downstream impact must
be measured separately in the corrected replay. Do not subtract $61.3 million
from the previously reported venue gaps or claim they are resolved.

Daily evidence and method/input hashes are retained in
`reconciliation/spark_anchorage_capital_policy_bridge_2026_08.json.gz`.

## Methodological difference still to separate

Original allocated capital is not always equal to outstanding lender-funded
principal. For example, a 100-dollar investment can remain intact while 10
dollars of separately earned cash repays Sky debt. Original capital stays 100;
the retained proportional financing model reduces Sky-funded principal to 90.
No transfer is missing in that example. A regression test checks this behavior.

Consequently, remaining `capital_basis_difference` values must be decomposed
into policy mismatch, repayment/refinancing effects and genuinely unsupported
funding. Never assign the difference to Sky merely to force equality. The
amounts for those remaining categories are not yet quantified.

## Next validation

Run the corrected funding replay with the accepted return policy for all 28
allocations and six custody rows. Preserve the pre-fix result for comparison.
Compare daily principal, uncertainty and conditional costs, prioritizing S4,
S28, S32, S1 and S5. This historical policy correction invalidates old funding
snapshots; subsequent reporting on the corrected snapshots can reuse them.

S66 still lacks an August revenue row and remains funding-only. No API
publication, settlement restatement or merger is authorized by successful
execution alone.
