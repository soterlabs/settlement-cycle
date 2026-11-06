# Spark spUSDS funding origin — August 2026

The receipt audit increases S1's traced original borrowing from
$662,616,464.85 (97.20%) to **$680,682,578.94 (99.846%)**, with
**$1,050,036.60** still unclassified. Closing acquisition capital is unchanged
at $681,732,615.54. This is a month-end funding-origin result, not a monthly
average, outstanding-debt reconciliation or production accounting change.

The previous conservative transaction-wide bound charged other allocations
against Sky borrowing even when their funds came from separate Savings token
receipts. The new diagnostic authenticates the actual USDS route:

- Spark ilk Vat frob, AllocatorVault Draw/Wipe, and normalized net debt agree.
- Minted USDS passes through the configured Buffer to ALM, then to spUSDS.
- SparkLend's Supply event identifies USDS, ALM and the deposited amount.
- The normalized position addition matches that cash payment; extra spUSDS
  transfers are rejected. The spToken mint transfer includes accrued interest,
  so its amount is not used as borrowed principal.
- Every ALM USDS transfer reconciles to opening/closing cash. Opening cash and
  other receipts remain unclassified; other payments consume borrowing first.
- Savings Take events reconcile independently to actual underlying receipts.

For example, July 28 transaction
[0x917e…0172](https://etherscan.io/tx/0x917eae55f38fcac8256362a3ed5fca0bb613e4568e18c0018e17ba65597a0172)
draws 7,222,021.056519919397347579 USDS into spUSDS while separately taking
49,298,987.779554 USDT from Savings. Existing USDS cash supplies the remainder
of the spUSDS deposit and is not relabelled as borrowing.

Receipt-proven contributions replace the old bound rather than being added to
it. Subsequent withdrawals release weighted-average borrowed acquisition basis.
All January–August research snapshots are regenerated; published settlements
and API revenue are unchanged. The original research artifact is retained.

Reproduce from the already adapted, previously reviewed S1 history:

```sh
PYTHONPATH=src:scripts .venv/bin/python scripts/audit_spark_spusds_draws.py \
  --normalized-history tests/fixtures/spark_spusds_origin_history.jsonl.gz \
  --receipts tests/fixtures/spark_spusds_draw_receipts.json.gz \
  --output reconciliation/spark_spusds_receipt_funding_origins_2026_08.json.gz
```

Tests cover the real mixed USDT/USDS transaction, changed recipients and
amounts, false debt, duplicate logs, failed receipts and unexpected spToken
payers; the existing sUSDS receipt tests also run after sharing draw validation.
S34 and S23 remain separate work items toward the requested 99% target.
