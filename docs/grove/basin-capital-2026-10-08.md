# Basin ownership follows internal shares, not one escrow's cash

The July 29 PAU repayment of **111,084.668810 USDS** was funded by a withdrawal
from JTRSY Basin. Its `Withdraw` event burns **111,083.451182731012430200**
internal shares and returns USDC, which is swapped through LitePSM and repaid
in the same transaction:
[July 29 transaction](https://etherscan.io/tx/0x079475f4b28e2f14e2a031a91e49b7f55a8956a178411edef9ff8cb337808d11).

The prior capital tracer represented this investment using E41's escrow USDS.
That is pool custody, not LP ownership: a USDC withdrawal need not reduce the
USDS balance in its pocket. It therefore missed the released investment basis,
classified repayment funding as unknown, and spread that uncertainty forward.
The raw escrow approach also missed later Basin investments.

The correction reads the PAU's `Deposit` and `Withdraw` events on both configured
Basins, preserving their actual internal share quantities. Event asset amounts
supply cash execution values; redeemed fractions release proportional borrowed
basis. Gains never become new borrowing. Pool/pocket movements and other LPs
are not separate PAU allocations. E41's raw escrow is marked as covered by the
JTRSY Basin allocation, preventing double-counting. Both Basin rows are financing
diagnostics only: event marks do not supply daily NAV or reported revenue/APY.

Fresh extraction and the reviewed historical adapter use the same share logic.
Third-party operations, unknown assets and fee shares accrued directly to the
PAU fail explicitly rather than inventing a funding attribution. Current observed
operations use USDS deposits and USDC withdrawals only; full exits release all
shares. The adapter is idempotent and honors pinned history cutoffs.

The precise July 2 onboarding spell and immutable Basin source links are in
`normalize/allocation_basin.py`. The twelve reviewed operations are checked
against canonical events and actual token transfers in
`tests/fixtures/grove_basin_capital_events.json`.

## August diagnostic replay

- Unmatched receipts: **26 → 22**; outflows: **256 → 244**.
- ALLOCATOR-GROVE-A traced costs: **$1,328.22 → $11,784.59**.
- Unchanged global GROVE-A cost: **$11,784.74**, leaving **$0.143297** unresolved.
- ALLOCATOR-BLOOM-A still has no fully eligible cost subtotal. Its unresolved
  funding paths must be identified separately; this does not reconcile all Grove.

The remaining GROVE-A difference is outside the narrowed diagnostic interval,
so the check correctly continues to fail its one-cent tolerance. No published
settlements, API values, revenue policy, debt or global borrowing charges changed.
Full before/after evidence: `reconciliation/grove_basin_capital_2026_08.json`.
