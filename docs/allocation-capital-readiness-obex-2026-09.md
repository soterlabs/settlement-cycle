# Allocation capital and borrowing-cost readiness — September 2026

Daily principal history: January 1-2026-09-30. Costs: September 2026, under the operator-confirmed funding assumption.
Savings dependency means a possible mixture, not proof that the entire position is Savings-funded.
Reinvested gains are excluded. Published revenue, settlement amounts and API outputs are unchanged.

| Prime | Venue | Allocation | Monthly average principal | Monthly borrowing cost | Status |
|---|---|---|---:|---:|---|
| obex | V1 | Maple syrupUSDC (Category B — ERC-4626 vault) | $390,349,308.44 | $1,198,885.71 | calculable under assumption |

## Interpretation

- Calculable means conditional on the operator-confirmed funding assumption and normalized movements, not independently proven funding origin.
- Principal is carried through reallocation; classified rewards and realized gains do not become borrowing. Passive NAV growth adds no principal.
- Savings dependency is tested throughout the selected month, propagates through transfers, and clears on a full exit. Mixed transactions are conservative; this does not claim a Savings percentage.
- EOA interiors are covered by their boundary, not charged again as independent allocations. Known Grove PAU/Basin/Paxos custody rows use their existing financing rules even without a revenue row. Missing revenue still leaves their net PnL unavailable.
- The existing accepted full-principal Anchorage return policy is reused. Its remaining blocker here is the Savings split.
- Exact available daily idle-USDS and SDE deductions are applied. The charge cannot be negative; excess deduction credit is recorded separately in JSON for the global reconciliation adjustment.
- Zero principal and exempt USDS have zero borrowing cost. Income-only rows and Savings liabilities are not independent investment allocations.
- This report does not assert that the ready subtotal equals global borrowing costs. Savings-dependent allocations, custody metadata, MSC debt and other prime-level adjustments remain outside that subtotal.

## Reproduce from existing snapshots

Use report_allocation_capital_readiness.py with --month, --settlements-root, both normalized histories and their matching idle-deduction files.
A non-August history must carry a pins.json manifest matching the requested month and settlement block pins. Use --apply-capital-adapters for freshly normalized inputs.
The compressed artifact contains daily principal, dependency days, costs, excess deduction credits, reused revenue and input/source hashes.
