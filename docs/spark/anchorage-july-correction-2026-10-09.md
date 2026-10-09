# Anchorage July duplicate-disbursement correction

The July 21, 2026 cash movements are:

| Transaction | ALM cash flow (USDC) |
|---|---:|
| [First disbursement](https://etherscan.io/tx/0xd3914263b329373b4e2845b67bfaae97214a9283d6c98604c38d007062fac475) | −10,000,008.643597 |
| [Second disbursement](https://etherscan.io/tx/0x7585f6cf59a793ca6b41e27220fc698999b69f48f22701d1e7b3b5871ca96b22) | −10,000,008.643597 |
| [Refund](https://etherscan.io/tx/0x4ad39783662761407cc239326f07ebe8102485838934f03a94d1b59bec512c69) | +10,000,017.287194 |
| Net deployment | **−10,000,000.000000** |

Complete receipts are saved in `tests/fixtures/spark_anchorage_july_correction.json.gz`.
The first two executions draw the disbursed cash from Sky. The existing July
rollover comments in `config/spark.yaml` and `QUESTIONS.md S32(b)` already
identify the same-day refund as a capital correction.

The report's day-net treatment did not book this refund as income. The tracing
normalizer, however, initially labels individual transfers from the escrow as
income. Its day-net exception matcher only handles positive net days, so this
net-outflow day did not remove the label. The result was both an overstated
facility claim and falsely earned cash in the capital ledger.

The normalizer now recognizes this exact refund and reduces the funded claim.
An idempotent adapter repairs preserved diagnostic snapshots, including any
subsequent opening balances. It accepts already-corrected fresh extraction and
rejects changed receipt metadata, cash amounts and partial transformations.
It preserves every debt movement. No published income or settlement is changed.

Tests reproduce the raw cash legs and the exact 10m net deployment, run the
normalizer on the net-outflow day, and replay the refund with borrowed basis.
This is separate from the still-unconfirmed July 16 and August 17
principal/interest splits.
