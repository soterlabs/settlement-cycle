# August 12 Binance OTC tests: trace the approved allocation boundary

The [June 18 Spark spell](https://github.com/sparkdotfi/spark-spells/blob/dc2a653f4b2f5491641276e913cae06e221ce8ea/archive/20260618/SparkEthereum_20260618.sol#L43)
authorizes Binance exchange entrypoint
`0xd010b876696f345d9e0a1b70f573244fcc2e0a0e`, OTC buffer
`0x1851c64bbfad132cbe75481f1690c381288ea492`, and USDT/USDC.
The controller's `OTCSwapSent` and `OTCClaimed` events identify the exchange,
buffer, token and amount. Actual token transfers independently agree.

The complete ALM/boundary history through block 25,878,704 contains:

| August 12 transaction | Boundary cash |
|---|---:|
| [First send](https://etherscan.io/tx/0x2d4f499b6eadce1aab46192756e020b419ddecb56e7980672f7c0848d7763dcf) | 2,000 USDT deposited |
| [Buffer claim](https://etherscan.io/tx/0x7e5bee5a406bd57995ea23849f961defca52051c168a6bc9e9052331687d1a93) | 2,249.428190 USDT returned |
| [Second send](https://etherscan.io/tx/0x1927ade1558dd5fbd33abe0e784d207648372593811bb7c469a88574ed46b6a2) | 2,000 USDT deposited |

For the first send, the only USDT ingress is a 2,000 Take from Spark Savings
USDT. Its only egress is the 2,000 OTC send. The independent Sky draw of
3,556.808411972098839995 USDS goes to spUSDS and spDAI. The adapter separates
these witnessed token routes, preserving both lenders' balances. The first
OTC deposit therefore does not acquire a share of the Sky draw simply because
both operations occur in one transaction.

Apply the boundary policy conservatively: release at most the previously
funded **2,000** when the first claim arrives. The remaining **249.428190**
is still an unclassified receipt; it is not automatically interest or trading
profit. The second 2,000 remains in the tracing-only `S_BINANCE_OTC` allocation.
This principal-first release is an explicit attribution assumption, not a
counterparty-confirmed profit calculation.

The buffer's complete token history separately reconciles to pinned balances
of **1,614.800000 USDC** and **630.491920 USDT**. Those balances are evidence that
cash remains at the approved boundary. They do not create another allocation
on top of the outstanding claim, additional borrowed principal, or a new idle
balance deduction. No tracing inside Binance's commingled wallets is needed.

`tests/fixtures/spark_binance_otc_history.json.gz` preserves all matching
transactions, original normalized batches, the buffer history, query filters
and pinned balances. Tests authenticate controller events against cash, check
the independent lender routes and Sky-debt conservation, retain the excess
receipt, and reject missing history or partial transformations. Fresh histories
without the optional Savings funding patch keep the boundary link but cannot
invent the missing saver funding; it remains uncertain.

The full replay at `546c8ae` was already running when this route was found;
its results must not be described as including this later adapter. This change
is capital analytics only and does not alter published revenue or settlement
charges.

Validation of all 451,911 adapted transactions preserves every daily Sky debt
amount exactly (100-digit comparison) and passes repeat-application checks.
Compared with the frozen replay's static inputs, gross unexplained outflows
fall by 4,000 USD and unexplained receipts by 2,000 USD. These are transaction
values, not borrowing-cost improvements. The full unit suite passes 1,904
tests, with one existing optional-dependency skip.
