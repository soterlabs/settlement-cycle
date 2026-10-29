# Spark sUSDS funding evidence across January-August

PR #215 diagnostic research. This expands receipt selection from August
survivors to material residual contributions at every January-August month-end.
It changes no production settlement, API revenue, debt or borrowing-cost data.
Pre-2026 transactions establish opening funding only.

## Month-end coverage

| Month | Previous traced percentage | Updated traced percentage | Remaining origin, USD millions |
|---|---:|---:|---:|
| January | 20.93% | 82.23% | 69.758 |
| February | 16.00% | 81.72% | 145.650 |
| March | 4.69% | 89.20% | 147.843 |
| April | 37.25% | 87.70% | 211.561 |
| May | 57.41% | 89.90% | 159.028 |
| June | 48.28% | 88.51% | 134.806 |
| July | 87.79% | 89.26% | 61.639 |
| August | 90.56% | 92.18% | 54.961 |

These are percentages of acquisition capital remaining at each cutoff, not
monthly averages, current debt allocation or Prime-wide coverage. Monthly
improvements must not be added: the same capital can survive several months.
The complete capital, traced borrowing and residual values are in the
[monthly report](../funding-origin-monthly-2026-01-08.md).

## Bulk evidence and recurring routes

The receipt cache now contains 1,362 Ethereum transactions (1,236 additional
receipts). The unchanged core requirements still authenticate the Vault draw,
signed Vat debt, minted USDS, Buffer transfer, ordered ALM cash movements,
Savings transfers and the sUSDS deposit/share movements. Configuration is loaded
once per audit rather than once per receipt; the validation criteria do not
change as a consequence of that optimization.

1,108 routes pass the draw validator. A further isolated spUSDS reallocation
passes its separate validator. The remaining 253 candidates stay unclassified
by these receipt checks; a baseline attribution may still apply independently.
Missing debt evidence, additional share movements, multiple/missing draws and
Savings mismatches are not silently accepted.

Two large March examples:

- [March 29](https://etherscan.io/tx/0x18a868a84346a0c2ca648315aca83a9aa949c205bf7a9b54dc7a0408730b292a):
  149,999,123.096248 USDS drawn and deposited into sUSDS; Savings USDT is a
  distinct funding leg.
- [March 21](https://etherscan.io/tx/0x8169a8a6f61747f73bf5194d945271592830019e5f1f8c80b321314e9006c873):
  171,000,014.061514 USDS drawn/deposited, followed by forwarded sUSDS shares.
  Only 170,000,013.669279 of the contribution is attributed to the retained position.

## Payments before the deposit

The optional `--allow-prefix-payments` path admits prior USDS outflows without
assuming they were paid with someone else's cash. It tracks the ordered cash
prefix, starts opening cash as unclassified, recognizes only the authenticated
Buffer draw as borrowed, and charges every other outgoing payment to borrowing
first. The sUSDS deposit then spends unclassified cash first. Forwarded sUSDS
value is again deducted from the proven contribution. The result is a lower
bound, not a claim of unique fungible-token provenance.

[March 22](https://etherscan.io/tx/0x9924ef53ce3e3a16c6dc01ecbb8329cb449e1291d07b9399591288ea138239aa)
draws 46,029,234.002423 USDS, pays 29,276.682582 elsewhere, deposits
45,999,957.319841 into sUSDS, and forwards some shares. Only
44,999,957.305832 remains attributed. Self-transfers and impossible cash
balances are rejected; failed evidence never bypasses the existing checks.

## Reallocation from spUSDS

[May 22](https://etherscan.io/tx/0x49e2233a80df690363e35626f30ac8ac6be8a638ad07dc23640a5aa33716a3e8)
withdraws 54,600,216.250783 USDS from spUSDS and deposits exactly that cash into
sUSDS. There is no new debt draw. The receipt has zero opening/closing ALM USDS,
one source ingress, one matching destination payment and matching deposit/share
events. The already-reviewed S1 origin ledger releases **49,437,308.684561** of
borrowed basis; only that amount is carried into S32. The remaining cash is
unclassified, not relabeled borrowing or booked as revenue.

The source basis is removed from S1 by its own withdrawal and credited once to
S32; it is not added as a fresh draw. If the source history contains no proven
borrowing, the receipt contributes zero borrowed origin despite matching cash.
Other source/destination movements, gifts and non-isolated cash require separate
evidence. The new route is opt-in and preserves all original capital balances.

## Reproducible collection and replay

`collect_spark_origin_receipts.py` ranks surviving residuals separately at each
month-end and deduplicates Ethereum transaction hashes. Adapter/custody identities
remain visible in the plan but are not sent as RPC hashes. The collector supports
resumption, atomic checkpoints, bounded concurrency and retries. Provider error
messages are not logged because they can contain credentials. Selection alone
never assigns an origin; the separate validator decides which receipts qualify.

```sh
PYTHONPATH=src .venv/bin/python scripts/collect_spark_origin_receipts.py \
  --history /tmp/pr215-spark-v1-history.jsonl.gz \
  --origin-report reconciliation/spark_susds_all_month_origins_2026_08.json.gz \
  --start 2026-01-01 --end 2026-08-31 --limit 250 --minimum 500000 \
  --plan /tmp/spark-next-origin-plan.json.gz \
  --receipts /tmp/spark-next-origin-receipts.json.gz --fetch
PYTHONPATH=src .venv/bin/python scripts/audit_spark_savings_origin.py \
  --history /tmp/pr215-spark-v1-history.jsonl.gz \
  --receipts tests/fixtures/spark_susds_all_month_receipts.json.gz \
  --start 2026-01-01 --end 2026-08-31 \
  --allow-mixed-prefix --allow-prefix-payments \
  --include-native-returns --include-spusds-returns \
  --output reconciliation/spark_susds_all_month_origins_2026_08.json.gz
```

The raw receipt fixture, input/source hashes, accepted/rejected proofs, source
ledger, destination ledger and remaining contribution ranking are retained.
Tests cover actual large March draws, the intervening payment and forwarded
shares, receipt tampering, conservative cash consumption, spUSDS source-basis
conservation, no invented borrowing from an unfunded source, collector retries
and resumption, and full-history versus compact-fixture replay agreement.
