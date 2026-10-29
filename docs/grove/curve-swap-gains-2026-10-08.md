# RLUSD/USDC swap gains are earned funding

Ten remaining receipt gaps are gains realized when Grove swaps between USDC
and RLUSD. The pool is explicitly authorized by the
[October 30 Grove spell](https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20251030/GroveEthereum_20251030.sol):
`0xd001ae433f254283fece51d4acce8c53263aa186`.

The pool's `TokenExchange` events and the ALM's actual input/output transfers
confirm **$1,123.768286697514264239** of gains across these transactions, using
the existing par valuation for both stablecoins. The tracer previously matched
funding dollar-for-dollar and treated the excess received as unknown capital.

The normalizer now requires the authorized pool, ALM buyer, correct coin indices,
and exact matching token-transfer amounts before classifying a swap gain as
earned funding. A gain is recorded on the output cash leg even when that cash
is deposited into Aave in the same transaction and its net raw balance change
is zero. Tests verify that the resulting investment contains the original
borrowed basis plus earned value, without borrowing against the gain.

A reviewed adapter applies the same event-derived amounts to saved histories.
It validates the transaction block, time, cash account and actual net movement.
Canonical evidence: `tests/fixtures/grove_curve_swap_gain_events.json`.
This is financing provenance only; reported swap revenue and settlements are
not recalculated or given a new accounting policy.

August replay reduces receipt gaps **14 → 4**. The remaining items are:

- Two May 7 RLUSD payments totaling **$49,596**, payer not yet attributed.
- One July 9 USDC payment of **$1**, potentially an onboarding-test refund;
  evidence is still being checked.
- **$0.000000505865** on an aToken withdrawal used for debt repayment, where
  scaled-balance rounding differs from actual underlying cash.

Unmatched outflows remain 239; many are observed execution shortfalls and are
being distinguished from missing destinations. Eligible costs remain $11,784.59.
GROVE-A's $0.143297 residual is fully explained by the two documented PAU swap
shortfalls; BLOOM-A remains unresolved. No residual has been forced into a venue.

The broad unit/monthly integration run passes **1,703 tests**, with two
environment-dependent skips and seven pre-existing warnings. Evidence:
`reconciliation/grove_curve_swap_gains_2026_08.json`.
