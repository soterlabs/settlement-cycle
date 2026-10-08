# GROVE-A's remaining 14-cent borrowing-cost difference is explained

On August 19 the PAU drew $1m and swapped part of it in the configured
AUSD/USDC Uniswap V3 pool `0xbafead7c60ea473758ed6c6021505e8bbd7e8e5d`:

| Transaction | Paid | Received | Execution shortfall at par |
|---|---:|---:|---:|
| [USDC to AUSD](https://etherscan.io/tx/0xe10ab6c75c5dcaab66bbfd8e6357e3689e539e5096ad6c120889631e9ea812f0) | 750,000 USDC | 749,909.122974 AUSD | $90.877026 |
| [AUSD to USDC](https://etherscan.io/tx/0xa698be559ff6fd5ce8c6ac68856c6862e53dc4bc1a45d39b2ad62504ff8a16e5) | 250,000 AUSD | 249,980.760840 USDC | $19.239160 |

The exact shortfall is **$110.116186**. These are observed swap execution
amounts, including price impact/fees; this analysis does not assume the whole
difference is the pool's fee. Canonical token and Swap events are retained in
`tests/fixtures/grove_pau_swap_cost_events.json`.

That money is no longer in an investment, but its funding debt remains owed.
The tracer currently keeps it in unallocated transaction funding, outside the
eligible allocation subtotal. Applying the existing daily GROVE-A borrowing rate
from August 19 through August 31 gives **$0.143297151432**. This matches the
entire difference between **$11,784.593973304823** in traced allocation costs and
**$11,784.737270456255** globally. Before August 19 the allocation sum already
matches daily global costs. The daily unexplained remainder is below 1e-18 USD.

This is an explanation, not a synthetic holding or a balancing adjustment.
The existing strict allocation-only comparison remains $0.143297 short until
financed execution expenses have an explicit presentation policy. They must not
be described as MSC interest, earned investment capital, idle cash or a missing
$110 allocation. No debt, borrowing rates, settlement charges or revenue changes.

Full daily decomposition: `reconciliation/grove_pau_execution_costs_2026_08.json`.
BLOOM-A remains a separate, unresolved capital-tracing problem.
