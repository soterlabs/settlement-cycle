# USTB: recover actual subscription funding and reviewed cash redemptions

Six atomic subscriptions on April 7–22, 2025 paid **300,000,000 USDC** for
**28,190,693.110178 USTB shares**. The normalizer inherited S21's `const_one`
report-pricing convention, so it valued those share units as dollars. It left
most of each Sky-funded subscription in unexplained custody and later treated
redemption cash as unknown funding.

The [April 3 onboarding spell](https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20250403/SparkEthereum_20250403.sol)
sets up USTB subscriptions. Each actual execution has matching USDC transfers,
a USTB mint and a `Subscribe` event naming the paid cash, cash after fees and
shares delivered. All six have zero subscription fee.

## Use the token's execution oracle

Historical `superstateOracle()` calls identify
`0xe4fa682f94610ccd170680cc3b045d77d9e528a8`. It supplies real-time USTB NAV
from issuer checkpoints. All six execution prices reproduce the subscribed
shares to within one raw share; the residual USD is below $0.000011 per entry.
The issuer oracle rejects expired checkpoints itself; its round timestamp is
the execution timestamp because it computes a current value.

The [standalone daily Chainlink USTB NAV](https://data.chain.link/feeds/ethereum/mainnet/ustb-nav-per-share)
is independently available, but is not the same execution-time price. Its
historical quotes differ from these actual subscriptions and redemptions.
Using it as though it were the subscription price would introduce another
pricing discrepancy. The capital-only pricer reads the oracle selected by the
USTB token at the same block, checks decimals/round/maximum delay, and rejects
missing or invalid data instead of falling back to $1.

## Two reviewed cash associations

| OffchainRedeem | Shares burned | Execution NAV | Actual USDC received |
|---|---:|---:|---:|
| May 14, 2025 | 10 | 10.675986 | 106.76 |
| July 17, 2025 | 28,190,683.110178 | 10.752185 | 303,111,440.08 |

The exact burn requests are
[May 14](https://etherscan.io/tx/0xc235bec8032617180c9e38d7b914592910ff9330a2fc9327820b189d5f1f3e9d)
and [July 17](https://etherscan.io/tx/0x49eefbb55bcae635426ff7994dbcbe4c853cb0f230f6856a27be64b0e1ead466).
Their same-day direct USDC payments to Spark ALM are
[106.76 USDC](https://etherscan.io/tx/0x4967b2260d6f934a5c4b5c8374dde7c1102a33d955e598113be30ea051c16151)
and [303,111,440.08 USDC](https://etherscan.io/tx/0x056f664613a69e7785c321a3bea1bc0c047967f79aefcee808d533f240103480).
Both payments equal the respective burned shares times the token's execution
NAV when rounded to cents. The two burns exhaust exactly all issued shares.

These are **reviewed closed cash associations, not cryptographic request-ID
links**. There is no automatic nearest-date matcher or payer-wide income rule.
The full direct-payer inventory contains two additional December payments;
they remain outside this USTB adapter and are handled separately by the reviewed
USCC settlement groups in `uscc-capital-2026-10-08.md`. It does not
trace the payer's commingled wallet interior.

Each burn carries existing basis into a separate S21 receivable, and only the
reviewed cash receipt settles it. A source-only cutoff retains its funded
claim; a receipt-only cutoff cannot fabricate a loan. The net economic return
across the entire historical cycle is **3,111,546.84 USDC**, not newly reported
revenue or an August adjustment. The tiny difference between exact execution
NAV and cent-rounded cash creates no additional borrowed basis.

## Scope and validation

The historical adapter accepts the exact legacy share-unit snapshot or the
correct NAV-priced form, validates transaction metadata and marks, and is
idempotent. The normalizer now uses the actual oracle for capital tracing on
future extractions. `config/spark.yaml`, `normalize/prices.py`, published
settlements, API revenue, debt and global costs are unchanged.

Canonical receipts, historical oracle reads, standalone NAV comparison and
all four direct payer receipts are in `tests/fixtures/spark_ustb_capital.json`.
Tests verify the paid USDC, issuer event, shares, NAV, both cash associations,
invalid-price rejection, partial cutoffs and the complete principal/gain cycle.
The earlier full replay had **300m of unmatched outflows** across these eight
USTB transactions and **303,111,546.84 of unmatched receipts**. The isolated
corrected cycle has no residual above a cent. The restarted full Spark replay
will measure its actual effect on August modeled borrowing costs; this evidence
alone does not certify the remaining Spark funding history.
