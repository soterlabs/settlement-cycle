# USCC: paid subscription basis and settlement-day redemption NAV

Additional evidence resolves the cash associations left open in the earlier
`uscc-history-2026-10-08.md` inventory. Four payments to the exact issuer
entrypoint authorized by the [October 16, 2025 spell](https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20251016/SparkEthereum_20251016.sol)
fund three USCC issuances for **150,010,000 USDC**. Three later burns exhaust all
**13,265,483.981402 shares**; their grouped cash returns total **151,013,951.26 USDC**.

The missing evidence was the NAV **at cash settlement**, rather than only at
the earlier redemption request. The independent
[USCC NAV feed](https://data.chain.link/feeds/ethereum/mainnet/uscc-nav-per-share)
reproduces both delayed payouts within one cent:

| Cash date | Shares settled | Request-day value | Settlement NAV | Actual USDC |
|---|---:|---:|---:|---:|
| Dec 2, 2025 | 8,843,655.987600 | 100,545,921.553797 | 11.384682 | 100,682,211.13 |
| Dec 4, 2025 | 4,421,827.993802 | 50,320,468.896887 | 11.382564 | 50,331,740.13 |

The first cash payment settles two December 1 requests; the second settles the
December 3 request. Exact settlement-NAV products exceed cash by $0.006222 and
$0.006443 respectively. Both actual payments are the cent-truncated products.
Request-day NAV alone leaves apparent differences of $136,289.576203 and
$11,271.233113; those differences are changes in the receivable's value, not
new borrowing or an unexplained principal injection.

These are **reviewed closed cash associations, not shared on-chain request
IDs**. They use the complete issuance/burn inventory, exact configured issuer
entrypoint, independently quoted settlement NAV and direct ALM cash receipts.
No automatic nearest-date matching, payer-wide income classification or tracing
inside the common payer's commingled wallet is introduced. The other two
receipts from that payer belong to the separately reviewed USTB cycle.

## Capital treatment

Actual Sky-funded payments create subscription claims, including the 10,000
USDC onboarding payment and following 50m payment in the first group. Receipt of
the purchased shares releases all paid basis from that claim. An issuance NAV
below the paid trade price does not retire debt or destroy borrowed basis.

The normalizer now uses the separate USCC NAV feed for capital tracing. The
historical token's `superstateOracle()` is zero because on-chain subscriptions
were disabled; this is not evidence that a share is worth $1. The feed identity,
6 decimals, valid round and 95,400-second heartbeat are checked, without a stale
or $1 fallback. Historical adapters accept the precise old share-unit marks or
the corrected NAV marks and reject inconsistent snapshots.

Each redemption retains its funding in a receivable. The first two requests
share one reviewed receivable; the later request has another. Actual cash marks
and releases those claims at settlement. All NAV appreciation and the historical
**1,003,951.26 USDC** economic return remain non-borrowed value. This is not a
new revenue booking or an August/September settlement adjustment.

Source-only cutoffs retain funded claims. Receipt-only history cannot invent an
opening loan. Incomplete funded groups and altered cash amounts fail validation.
Tests reproduce both payment groups and the complete principal/gain cycle with
no material unmatched flow or realized principal loss. The full combined replay
will determine the effect on August modeled allocation costs; other unresolved
Spark funding still prevents certification.

The old checkpoint had **164,279,435.241402** of incoming residuals (the three
unfunded-looking issuances plus cash) and **163,275,483.981402** of outgoing
residuals (payments plus share-unit burns) across these 12 transactions. These
are gross historical tracing differences, not lost principal or monthly revenue.

Canonical events, all raw batches and request/settlement NAV calls are retained
in `tests/fixtures/spark_uscc_*.json`. Exact groups and hashes appear in
`reconciliation/spark_uscc_capital_evidence_2026_08.json`. Production report
pricing/configuration, published reports, API revenue and global debt/costs are
unchanged.

As a development-only plausibility check, the complete cash returns annualize
to approximately **6.55% for USCC** and **4.16% for USTB** using time-weighted
paid capital (end-of-day convention). Both are within the requested 0–8%
debugging range. These are isolated-cycle checks, not production APYs, and no
runtime bounds, publication warnings or blocking rules were added.
