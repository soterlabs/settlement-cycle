# Anchorage funding-origin receipt audit

August 31 acquisition principal remains **$208,829,226.694857** under the
previously accepted MSC full-principal treatment of July/August receipts.
Receipt checks increase proven original Sky funding from $148,274,582.04 to
**$201,630,012.24 (96.55%)**. **$7,199,214.46 remains unclassified**: this is
not yet the requested 99% coverage and is not a statement that every residual
dollar was borrowed. No published settlement or production API figure changes.

## Evidence

The audit verifies rate-normalized debt against gross Vault Draw/Wipe events,
then follows the actual USDS, DAI and USDC transfer logs through the existing
USDS/DAI converter and LitePSM to Anchorage's escrow. Each conversion must
have the matching input payment, USDS burn, DAI mint, PSM payment and USDC
receipt. It does not follow funds inside the escrow EOA.

- [May 10, $5m](https://etherscan.io/tx/0x9f1eea716acf5c781e19880d98fbe1745ba413c380eed29e941a952673e0dc7b):
  all $5m originates in the draw despite unrelated Savings repayments elsewhere.
- [May 11, $548,611.11](https://etherscan.io/tx/0x15e49a0ab289bbc05ff883138c3d2d09e85b4ff67f42653c8bf96c3b43e9064b):
  the draw also funds another allocation. Only the actual escrow payment is
  credited to Anchorage.
- [December 16 opening funding](https://etherscan.io/tx/0xeb7812c1bb950c16a4e70e7fb707b79a8cbf7a5e07c743d2decfcb36ad0b11ef):
  $1,002,961.928704 of a $1,618,055.553588 deposit is proven Sky funding.
  Opening cash and an incoming bridge mint remain unclassified here. A later
  wipe does not erase the original funding of the earlier escrow payment.

Every token's ordered transfers reconcile to its normalized opening/closing
cash. Opening cash and unrelated receipts carry no presumed borrowing. Route
payments spend unknown cash first; other payments consume known borrowing
first. These rules establish a conservative amount even in mixed transactions.
Authenticated Savings Take receipts are checked separately. Any route that
fails validation retains the prior direct-draw audit treatment.

478 new receipt proofs replace the prior transaction-wide bound; they are not
added twice. All principal returns still reduce traced borrowing first, matching
the existing facility convention. The separate outstanding-debt lower bound
uses at most the positive net draw when a receipt proves a larger gross draw,
so it cannot mistake later repayments for debt still outstanding.

## Reproduction

```sh
PYTHONPATH=src:scripts .venv/bin/python scripts/audit_spark_anchorage_draw_routes.py \
  --normalized-history tests/fixtures/spark_anchorage_origin_history.jsonl.gz \
  --receipts tests/fixtures/spark_anchorage_draw_receipts.json.gz \
  --output reconciliation/spark_anchorage_receipt_funding_origins_2026_08.json.gz
```

The fixture comes from the adapted full history with the existing Anchorage
round-trip correction and accepted full-principal returns already applied. It
retains complete facility transactions and every negative net debt transaction;
positive draws unrelated to the facility do not affect either facility origin
or its conservative outstanding-debt floor. Pre-2026 transactions establish
opening funding only. Production accounting remains scoped to 2026.

Next work: prove pre-existing cash and redeemed/bridged principal, recognize
separately evidenced non-Sky funding, and handle the remaining conversion paths.
