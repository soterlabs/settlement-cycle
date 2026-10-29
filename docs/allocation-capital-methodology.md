# Measuring allocation capital without proving its original funding source

## The distinction

We can observe **how much enters and leaves a venue** without knowing which
earlier Sky draw funded each deposit. Funding origin answers a different
question: how much of the position is financed by Sky, by Spark Savings, or by
earnings?

For example, 100m USDC sent from the ALM to the Galaxy Warehouse entrypoint is
a 100m contribution. It does not matter whether the ALM drew that USDC today,
held it overnight, or withdrew it from another allocation. The venue boundary
establishes the contributed amount. The funding mix determines its borrowing
charge, not whether the contribution can be measured.

This still requires observing deposits, withdrawals and their principal/income
classification. It does not mean using the present token balance as principal
or ignoring capital movements.

## Three quantities that must not be confused

| Quantity | Meaning | Example after a 100 deposit grows to 110 |
|---|---|---:|
| Current position value / NAV | Assets currently held, including accrued gains | 110 |
| Allocation acquisition capital | Amount invested at this venue boundary, reduced by the cost basis of exits | 100 |
| Sky-funded original principal | The part attributable to borrowing, excluding Savings and gains | 100 if initially all Sky-funded |

If the position is fully redeemed for 110 and all 110 is deposited into another
venue, the new venue receives 110 of acquisition capital. Its inherited original
principal is 100; the other 10 is reinvested gain. Charging interest on 110 would
violate the rule that gains do not create borrowed principal.

The new diagnostic's `daily_principal` and API's
`average_original_principal_usd` refer to original principal carried through
reallocations, **not NAV or the sum of gross deposit turnover**. Where Savings
funding remains mixed, this amount is not asserted to be entirely Sky-funded.
`average_yield_exposure_usd` is a separate denominator used for returns.

## How capital is measured

1. Start with verified opening capital, by holding and pending-custody account.
   A reusable January 1 checkpoint avoids replaying older history each time.
   A first observed balance/NAV is not silently treated as borrowed principal.
2. Add actual acquisition payments. Use deposited underlying for ERC-4626,
   execution-index-normalized aToken/spToken events, actual LP capital events,
   and subscriptions/redemptions for tokenized funds.
3. Exclude explicitly recognized rewards, reserve-factor receipts, fees and
   other income from new borrowed principal. Passive share-price appreciation
   does not add principal.
4. On a partial withdrawal, release the corresponding weighted-average cost
   basis. The difference between cash proceeds and recovered principal is gain
   or loss. On reallocation, carry the recovered original principal; do not
   turn the full cash proceeds into a new loan.
5. Keep pending subscriptions and redemptions in the allocation until the
   linked cash/custody movement settles. Token burns and delayed cash receipts
   must not create artificial gaps or duplicate contributions.
6. Authorized EOAs are allocation boundaries. Deposits establish the venue
   claim; classified principal returns reduce it. We do not investigate funds
   inside a commingled FalconX or loan-provider wallet.
7. Snapshot the principal after each UTC day, using the existing end-of-day
   financing convention. August's average is the sum of its 31 daily balances
   divided by 31; gross turnover is not the average capital.

The allocation-local identity is:

```text
closing acquisition capital
  = opening acquisition capital
  + new contributions
  - acquisition cost released or consumed
```

Custody movements inside the same allocation cancel. Cross-allocation transfers
release the source and fund the destination; they are not two new loans.

## What the operator assumption changes

The accepted assumption is that principal is Sky-funded except Spark Savings
funding. This removes the need to prove every unmatched principal inflow back
to an individual `frob` or original mint. It does not turn known income into
principal or resolve the Savings mixture by fiat.

The diagnostic reuses normalized transaction movements and weighted-average
principal propagation. It therefore follows immediate cash/reallocation links
to keep realized gains out of original principal, but does not require proof
of the ultimate minting origin for every deposit.

Savings dependency propagates conservatively from authenticated external
funding operations through connected movements. A mixed transaction may mark
more than one potential destination. That flag establishes a possible mixture,
not its amount. A full exit clears it; a partial exit does not. Quantifying
Savings repayment/refinancing is the next research step, documented separately
in [the funding PRD](PRD_allocation_funding_next_steps.md).

## Borrowing costs, deductions and yields

For days with a usable Sky-funded basis, apply the existing daily financing
rate and the existing dollar deductions. Original principal and actual
outstanding ilk debt are different ledgers: repayments, capitalized interest,
MSC debt and financing outside allocations need a separate reconciliation.

- Idle USDC, USDT, PYUSD, AUSD and other non-USDS stables are **not exempt just
  because they sit at an ALM**. Their Sky-funded principal incurs borrowing
  costs. If they earn no income, the net return on that charged portion is
  negative.
- Gains held as cash are still gains, not newly borrowed principal. This can
  make an idle account's charge much smaller than its NAV times the rate.
- Apply the existing idle-USDS and SDE deductions, including effective dates
  and ownership. Basin's additional GROVE-A-only idle-USDS deduction starts
  September 1, 2026, not August.
- A deduction exceeding an allocation's principal is retained as a separate
  credit for reconciliation; it is not presented as negative borrowing cost.
- Reuse the existing period revenue. Gross yield uses booked actual plus
  external revenue. Net uses Prime-retained venue revenue after SDE, less
  borrowing cost; it excludes prime-level adjustments/spread reimbursements.
- The return denominator is the published time-weighted position value or
  notional. Anchorage instead uses the independently reconstructed average
  outstanding loan principal, because its pass-through escrow is not the loan.
- Monthly return is period profit divided by average exposure. APY is
  `(1 + monthly_return) ** (365 / days_in_month) - 1`. Cash-recognized income
  gives a realized-period yield, not necessarily a contractual accrual rate.
- Missing financing produces a null cost/net yield, not zero. Missing revenue
  does not prevent computing a known cost, but it does prevent inventing PnL.

## Reusable implementation and scope

- `scripts/audit_allocation_positions.py`: allocation-local acquisition basis,
  custody, daily checkpoints and the capital identity.
- `scripts/report_allocation_capital_readiness.py`: original principal carried
  through movements under the accepted assumption, Savings dependencies,
  monthly borrowing costs and exact available deductions.
- `scripts/report_allocation_yields.py`: reuses booked revenue and the modeled
  monthly costs; no RPC or income recalculation.
- `scripts/publish_monthly_allocations.py`: versions those reviewed outputs
  separately for the read API; publication never updates settlements or daily
  revenue records.

The current dataset covers January-August daily capital and August monthly
analytics for Spark and Grove. These are assumption-based allocation analytics,
not a restatement or certification of the total Prime settlement.
