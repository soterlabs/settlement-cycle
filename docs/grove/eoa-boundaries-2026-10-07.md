# Grove capital replay at configured EOA allocation boundaries

Operator instruction: stop tracing capital at configured deposit/return EOAs.
Their other operations and commingled balances are outside the allocation
perimeter. Implemented in draft PR #215; no settlement or API revenue update.

## Applied boundary and recognition convention

The existing E36 FalconX configuration supplies both endpoints:

- Deposit: Ethereum ALM sends USDC to
  `0xd94f9ef3395bbe41c1f05ced3c9a7dc520d08036`.
- Return: `0x94b398acb2fce988871218221ea6a4a2b26cccbc` sends AUSD to the
  Ethereum ALM (anchor E14).

Each deposit creates a claim at E36 carrying the cash's existing borrowed
basis and originating ilk. Returns release that basis back into ALM cash.
The existing paired-principal-cap convention reduces outstanding deposited
value first and recognizes excess receipts as realized gain. Weighted-average
borrowed versus earned funding is preserved; reinvested gains do not become
borrowed capital. This is a cash-return convention, not an assertion that a
partial withdrawal contains no economically accrued interest. Unreported NAV,
terminal losses and intermediate wallet balances are not inferred.

The source query reads only ALM boundary events. It no longer reads balances,
NFT events, swaps or lending positions inside these configured EOA wallets.
E30/E31/E32/E33/E25/E34/E35 are marked `covered_by_boundary: E36` for capital
analytics. Their existing settlement revenue calculation is unchanged. They
receive no separate borrowing cost or net APY; E36 holds the funding claim,
avoiding duplicate allocation costs. Nonzero interior idle/SDE deductions
require explicit attribution rather than silently disappearing. All such
interior deductions are zero in the checked August control.

Galaxy E42 already uses this boundary approach: USDC sent to its configured
principal counterparty becomes a facility claim, without tracing the wallet's
subsequent activity. Yield-payer addresses are not automatically treated as
principal-return addresses. Unknown EOAs are not assigned to invented venues.

## Verified FalconX history through August 31, 2026

| Item | USD at configured par valuation |
|---|---:|
| USDC deposits, Nov–Dec 2025 (four transfers) | 50,001,000.000000 |
| AUSD returns, Feb–Apr 2026 (twelve transfers) | 50,120,746.468785 |
| Realized excess under the principal cap | 119,746.468785 |
| Outstanding E36 principal in August | 0.000000 |

A $1,000 test payment is part of the actual deposited total; the older rough
$50m description omitted it. The last return on April 24 exhausts the claim.
A fully exhausted claim now clears its own funding uncertainty while its
returned cash retains any inherited uncertainty. This avoids marking a closed
allocation unresolved forever. Existing unexplained source events remain in
the diagnostic audit; no uncertainty is erased from active proceeds.

## August replay and reconciliation

Fresh replay of the prior normalized Grove history, with the same pinned
August debt, MSC and daily idle controls. The targeted migration removes
interior-wallet movements and inserts the 16 canonical boundary events.
Original transaction debt changes and timestamps are retained. This is not a
fresh extraction of every other venue.

| Diagnostic | Previous replay | EOA-boundary replay |
|---|---:|---:|
| Unmatched receipts | 267 | 242 |
| Unmatched outflows | 490 | 468 |
| Eligible allocation borrowing-cost subtotal | 1,233.51 | 1,233.51 |

| Ilk | Eligible allocation cost | Global CoF excluding MSC | Remaining gap |
|---|---:|---:|---:|
| BLOOM-A | 0.00 | 3,448,941.99 | 3,448,941.99 |
| GROVE-A | 1,233.51 | 11,784.74 | 10,551.22 |

Neither ilk reconciles. The unchanged eligible subtotal is an all-or-nothing
completeness result, not proof that only that much economic cost has been
traced. Earlier uncertain receipts and repayments still affect later positions.
The EOA simplification removes unnecessary investigations inside commingled
wallets; it does not establish the funding classification before deposits.

The change does recover attribution into subsequent allocations. For example,
E6's modeled average borrowed basis rises from $482,089.84 to $5,598,078.19;
its provisional August borrowing-cost arithmetic changes from $1,494.71 to
$17,356.72. Galaxy E42's corresponding estimate moves from $922,702.27 to
$925,434.79. These remain unresolved estimates, excluded from net PnL/APY and
from the eligible subtotal. Do not present them as validated charges. Summing
all unresolved estimates is misleading: incomplete basis can be smaller than
the unchanged SDE deduction and produce negative modeled costs.

Next material source gaps remain Ethereum-to-Plume JTRSY custody and other
ALM-boundary flows. Tiny BUIDL-payer receipts also expose the existing binary
completeness limitation; resolving or bounding those uncertainties remains
separate work. No permanent APY warnings or publication blockers were added.

## Evidence and checks

`reconciliation/grove_eoa_boundaries_2026_08.json` contains all 16 boundary
logs, amounts, per-ilk reconciliation, allocation rows and input hashes.
`reconciliation/allocation_resume_2026_08.json` retains the exact daily control
inputs. Local replay outputs are under `/tmp/pr215-grove-eoa/`; the matched
baseline was replayed with the same closing-claim rule.

1,436 unit/monthly computation tests passed, with two environment-dependent skips (optional Keccak dependency and isolated PostgreSQL URL). Ruff and git diff --check passed. Published Grove and Spark August provenance hashes are unchanged.
