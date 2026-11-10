> **Expanded replay in progress:** see [scope-status.json](scope-status.json). Previous totals and October values below are provisional until #220 is included and overlaps/prior credits are netted. PR #277 is back in draft.

# January-September 2026 incremental reconciliation

Baseline: [`536f149`](https://github.com/soterlabs/settlement-cycle/commit/536f1492089ed82fbf6eaabb956f7a194b887d47).
Calculation target: [`bda6858`](https://github.com/soterlabs/settlement-cycle/commit/bda68582defce86daeb1c7d5d3f6d5da03f18df9),
including merged PRD #263 and its separate implementation #278. PR #277 was
rebased onto this main before the final calculation. The inventory classifies
all 49 merged PRs in the range.

This is an incremental component reconciliation, not a new monthly settlement
run or an ALM transfer-review attestation. It writes no frozen settlement,
production adjustment, database revision, API result or executed payment.

## Booked historical components

| Prime | Supply-side adjustment | Sky-share adjustment | Send-only refund |
|---|---:|---:|---:|
| Spark | 1,338,239.62 | -255,869.48 | 0.00 |
| Grove | 8,249.94 | -110,419.56 | 0.00 |
| Osero | 0.00 | 0.00 | 5,514.00 |
| Obex | -273,508.90 | 0.00 | 0.00 |

Negative Sky-share values give debt-mint relief. Positive/negative supply-side
values add income/recover overstated income. Osero's refund increases only its
transfer. CoF credits are not added again through supply-side adjustments.

Totals preserve cent rounding of each separately booked/approved component.
Grove's full Sky adjustment is therefore -110,419.56, although rounding all
raw components together would differ by a cent. Monthly values retain their
unrounded source calculations. Final MSC applies the ordinary supply-side
floor and whole-USDS rounding to actual October figures.

## Files and reproduction

- [Forum post](../../forum_posts/reconciliation-2026-01-to-2026-09.md): causes,
  earning months, prime/Sky effects, prior credits and exclusions.
- [data.json](data.json): monthly signed components, exact source hashes,
  full totals, already-configured credits, remaining additions and linear
  mint/transfer/Sky-settlement-net effects.
- [proposed-october-adjustments.json](proposed-october-adjustments.json): proposed
  **replacement values** for the four primes' October preview entries, preserving
  ordinary October receipts. These are not another adjustment layer.
- [inventory.md](inventory.md) and [pr-inventory.json](pr-inventory.json): included,
  zero-amount, documentation and reporting-only classifications.
- [venue-evidence.json.gz](venue-evidence.json.gz): retained RPC responses,
  complete specified owner/token histories and exact daily boundary witnesses.
- [rlusd-receipts.json](rlusd-receipts.json): six actual native RLUSD receipts,
  1,373,208 in September, checked against complete captured payer history.
- [dr-code-evidence.json](dr-code-evidence.json): pinned upstream DR workbook/CSV
  identity and source check for codes 3014/3017; no January-September addition.
- [Async audit](../async-allocations-2026-01_to_2026-09/README.md): #278's complete
  Spark/Obex component sweep, source histories, independent month-boundary
  ownership proofs, daily quotes, exact results and two review/fix rounds.

From the repository root:

```sh
SETTLE_NO_CACHE=1 python scripts/build_jan_sep_2026_reconciliation.py \
  --output-dir /tmp/jan-sep-2026-reconciliation
```

This rebuilds `data.json`, `forum-post.md` and the proposed October values using
production components and their existing retained-evidence replays. Network
connections are blocked. The async implementation's detailed replay command is
in its linked README. The aggregation verifies its implementation fingerprints
and consumes the already-replayed results; it does not invent new monthly input
certificates. Outputs are separated from historical `settlements/`.

## Booking once

Grove's -78,530.06 historical Sky adjustment and Osero's 5,514 refund are already
present in October configuration. They are included once in the full historical
ledger and subtracted from the remaining additions. Ordinary October partner
receipts remain outside the historical totals.

Additional values to add to the **existing** October configuration:

| Prime | SV | Sky share | Send-only refund |
|---|---:|---:|---:|
| Spark | 1,338,239.62 | -255,869.48 | 0.00 |
| Grove | 8,249.94 | -31,889.50 | 0.00 |
| Osero | 0.00 | 0.00 | 0.00 |
| Obex | -273,508.90 | 0.00 | 0.00 |

The earlier Spark reserve-factor and Grove BUIDL/CoF credits in the last forum
post are excluded. The August 2,508.55 CoF credit and 162,505.35 cash-fee credits
are explicitly deducted by their existing replays. October bridge effects and
October partner rebates belong in the ordinary October MSC, not this ledger.
The proposed values do not assert that the already-configured credits have
been executed. Production configuration remains unchanged in this PR.

## Validation

- Replays of missing venues, issuer receipts, original daily CoF, Grove small
  BUIDL costs and Osero executed payments use retained public inputs only.
- Gateway payloads are decoded/authenticated again at September's exact chain
  pins; post-September releases cannot remove an earlier claim.
- #278's before/after replay reproduces published Maple income and averages,
  with byte-identical repeat output and zero global-CoF delta.
- Financial regression checks cover historical cutoffs, prior-credit netting,
  preservation of ordinary October receipts, and mint/send effects through the
  existing production MSC calculation, including Osero's shortfall floor.

The native-USDS audit certifies only its four supported Ethereum routes. Other
bridge families and off-report payments are not inferred from absence in that
scan. Warehouse/Basin and economic-average changes can redistribute venue CoF
without creating another headline adjustment.
