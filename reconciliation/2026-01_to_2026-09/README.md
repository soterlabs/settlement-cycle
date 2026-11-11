# January-September 2026 reconciliation

Replay current applicable corrections starting with **PR #220**, through
[`bda6858`](https://github.com/soterlabs/settlement-cycle/commit/bda68582defce86daeb1c7d5d3f6d5da03f18df9).
Compare against frozen January-September reports and subtract previously settled
true-ups. September's settled code baseline is
[`536f149`](https://github.com/soterlabs/settlement-cycle/commit/536f1492089ed82fbf6eaabb956f7a194b887d47).
The inventory covers 57 merged PRs. Actual policy dates remain in place;
#220's documented rollout restriction was not a runtime date gate.

## Historical adjustments

| Prime | Supply-side adjustment | Sky-share adjustment | Send-only refund |
|---|---:|---:|---:|
| Spark | 249,141.52 | -255,869.48 | 0.00 |
| Grove | 11,554.74 | -110,419.56 | 0.00 |
| Osero | 0.00 | 0.00 | 5,514.00 |
| Obex | -273,508.90 | 0.00 | 0.00 |

All figures are USDS. Negative Sky-share amounts reduce debt minting. Positive
supply-side adjustments increase income; negative ones recover overstated
income. Refunds increase the transfer without another loan. These are historical
adjustments only; ordinary October partner receipts are excluded.

The exact lending replay adds **-1,089,098.10** to Spark's previous draft
correction and **+283.23** to Grove's. Spark's S70 missing-venue correction is
included once in the combined lending replay. Osero's income increases by
270.63842337876821683000, but its corrected share remains negative in every
month, so its already-financed shortfall creates no additional payout beyond
the 5,514 refund. The earlier event-principal estimate is superseded.

## Retained evidence and reproduction

- [Forum draft](../../forum_posts/reconciliation-2026-01-to-2026-09.md): causes,
  monthly lending changes, Osero netting, prime/Sky effects and exclusions.
- [data.json](data.json): signed monthly components, source hashes, net totals,
  already-configured credits, remaining additions and linear settlement effects.
- [atoken-inputs.json.gz](atoken-inputs.json.gz): 97,352 retained RPC responses,
  complete specified holder histories, verified frozen report inputs and
  monthly boundary states for 17 Aave/SparkLend allocations.
- [atoken-replay.json](atoken-replay.json): production results for all 139
  allocation-months, native/external changes, dated capital flows, implementation
  hashes and verified constant-scaled-state proofs for intermediate probes.
- [archive-verification.json](archive-verification.json): Ethereum mainnet and
  24 exact historical response cross-checks on the user's replacement archive
  node. No endpoint credentials are retained in these artifacts.
- [proposed-october-adjustments.json](proposed-october-adjustments.json): proposed
  **replacement** values preserving ordinary October receipts.
- [inventory.md](inventory.md), [pr-inventory.json](pr-inventory.json) and
  [scope-status.json](scope-status.json): complete scope and classifications.
- [venue-evidence.json.gz](venue-evidence.json.gz), [rlusd-receipts.json](rlusd-receipts.json)
  and [dr-code-evidence.json](dr-code-evidence.json): other retained component inputs.
- [Async audit](../async-allocations-2026-01_to_2026-09/README.md): #278's shared
  Spark/Obex audit, source witnesses, daily quotes and before/after results.

From the repository root, with the package installed:

```sh
SETTLE_NO_CACHE=1 python scripts/replay_atoken_reconciliation_2026.py \
  --inputs reconciliation/2026-01_to_2026-09/atoken-inputs.json.gz \
  --output /tmp/atoken-replay.json

SETTLE_NO_CACHE=1 python scripts/build_jan_sep_2026_reconciliation.py \
  --output-dir /tmp/jan-sep-2026-reconciliation
```

Both calculations block network access. The lending replay calls the production
capital, external-revenue and venue-revenue functions with exact retained
archive states and complete event histories. The builder validates the retained
results' input/configuration/implementation hashes, checks all expected
allocation-months, and nets their results with other production component
replays and prior payments. It preserves the current production rounding and
full-exit limitations; it does not introduce a different yield formula.

The capture script supports checkpointed live recapture using the locally
configured RPC and event-source secrets. Existing retained inputs suffice for
offline reproduction; access to those secrets is not required by reviewers.

## Booking once and practical limits

Spark's prior 2,392,354.07 reserve-factor credit and Grove's 165,013.90 BUIDL/CoF
credit remain handled once. Original effective dates are preserved. The
2,508.55 August CoF offset and 162,505.35 BUIDL fee offsets remain explicit in
their component replays.

Grove's -78,530.06 Sky adjustment and Osero's 5,514 refund are already configured
for October. Remaining additions to the **existing** October configuration are:

| Prime | SV | Sky share | Send-only refund |
|---|---:|---:|---:|
| Spark | 249,141.52 | -255,869.48 | 0.00 |
| Grove | 11,554.74 | -31,889.50 | 0.00 |
| Osero | 0.00 | 0.00 | 0.00 |
| Obex | -273,508.90 | 0.00 | 0.00 |

Each booked component is rounded to cents, preserving approved amounts. Final
MSC uses its normal floor and whole-USDS rounding on actual ordinary October
figures. Linear settlement-effect tables are explicitly conditional on those
floors; they are not a substitute for the actual October calculation.

This is a read-only component comparison. It changes no frozen settlement,
production configuration, database/API revision or executed payment. It does
not certify a new complete monthly ALM transfer review. Whole-prime CoF is
unchanged by lending/async venue-average reattribution; warehouse and Basin
presentation changes add no separate credit. The native-USDS bridge audit
certifies its four supported Ethereum routes, not every bridge family.

## Grove-only update after #280

The branch is rebased on `703c668` (#280 merged). Only Grove's affected nonzero
positions were replayed: 17 Base allocation-months in January-September, plus
E12's nonzero monthly reporting weights. Cash-method income +13,769.34 less the
inherited June published-boundary difference 10,747.77 adds **3,021.57** to
Grove's prior ledger. Grove's full income correction is now **11,554.74**.
Its -110,419.56 Sky adjustment is unchanged. The E43 missing-venue credit
remains counted once. Spark, Osero and Obex values/items are byte-equivalent
to `before-pr280.json`; their computations were not rerun.

The earlier 139-row lending replay remains an immutable pre-#280 checkpoint.
Reproduce that earlier source replay at `fd6d2ed`, with its matching config.
The current builder command above replays Grove only through
`scripts/update_grove_pr280_reconciliation.py`; it validates the checkpoint
hash and records per-prime calculation commits and new input hashes.

## Rebase after #282

Rebased on main `c559a19` after #282 merged. The retained offline builder was
actually rerun: the 17 active Grove allocation-months and E12 attribution
reproduce, and every financial item, total and proposed October replacement
is unchanged. Other agents and the 139-row lending replay remain verified
immutable checkpoints; this is not a new whole-prime monthly run.
See [post-pr282-validation.json](post-pr282-validation.json) for the command,
source revision and output hashes.

**Pending #281:** Spark's historical bridge adjustment in this draft still
requires the separately reviewed bridge-baseline correction. Its existing
Sky-adjustment figure is provisional and must not be booked as a final refund.
The #282 asset-control requirement has not been certified by this component run.
