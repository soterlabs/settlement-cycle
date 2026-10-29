# Ethereum Morpho V1 fee shares are income, not borrowed capital

Reviewing a May 25 USDC withdrawal revealed omitted performance-fee shares.
The same omission affects three Ethereum vaults: the existing V1 fee adapter
was restricted to the Base vault. The separate USDT V2 fix did not cover them.

| Allocation | Ethereum vault | Historical fee value through August 31 |
|---|---|---:|
| S10 Blue Chip USDC | `0x56a76b428244a50513ec81e225a293d128fd581d` | 1,984.118742 USD |
| S12 Spark DAI | `0x73e65dbd630f90604062f6e02fab9138e713edd9` | 506,772.090831 USD |
| S13 Spark USDS | `0xe41a0583334f0dc4e023acd0bfef3667f6fe0597` | 67,169.435618 USD |
| Total | | **575,925.645192 USD** |

These are cumulative tracing values, not August revenue, borrowing-cost savings,
or amendments to published reports. Fee shares belong to Spark but do not
establish any Sky-funded principal. Their later liquidation can release income
alongside a funded investment's principal.

The [MetaMorpho implementation](https://github.com/morpho-org/metamorpho/blob/ded84e59668155b34d3c24906c4f7461c12828af/src/MetaMorpho.sol)
mints fee shares immediately before its two-word `AccrueInterest` event. Match
the adjacent mint's vault, recipient, transaction and exact amount. Ordinary
deposit mints are separate. At block 25,878,704, `feeRecipient()` independently
returns the Spark ALM for all three vaults; that current state alone is not
used to classify historical transfers.

The full evidence fixture contains 201,931 selected logs and independent closing
share balances. Reconstructing every ALM share transfer from inception reproduces
all three pinned balances. The diagnostic repair changes 13,787 positions in
13,762 transactions and preserves debt fields and unrelated movements. It uses
six underlying decimals for USDC and eighteen for DAI/USDS. One initial DAI
deposit requires an independently queried, exact-block share quote; the other
fee values use recoverable snapshot marks or actual withdrawal cash per share.

The [May 25 USDC transaction](https://etherscan.io/tx/0xf0303a1a12da32434bb3a46433ecb3db98123621f1e31f28028c666300b09fad)
initially looked like an execution-price discrepancy. Its fee mint was missing
from the tracer. Recognizing the fee also restores the exact-withdrawal branch,
which separately identifies fee income and the actual cash returned. This is
not the same issue as transactions containing both deposits and withdrawals;
those still use the existing mixed-flow valuation policy.

The repair accepts the previously reviewed Paxos transaction suffix only when
explicitly requested for this V1 history. It checks the original transaction's
block, timestamp, account and amounts, preserves its boundary identity, and
rejects duplicate or missing fee contexts. Both V2 and V1 diagnostic repair
metadata remain in the output. No published report or API history is regenerated.

Reproduce against the separately fingerprinted pre-V1 tracing input:

```sh
PYTHONPATH=src .venv/bin/python scripts/repair_spark_morpho_fee_history.py \
  --history /tmp/pr215-spark-final-history.jsonl.gz \
  --events tests/fixtures/spark_ethereum_morpho_v1_fee_history.json.gz \
  --unit-prices tests/fixtures/spark_ethereum_morpho_v1_fee_unit_prices.json \
  --reviewed-ethereum-v1 \
  --output /tmp/pr215-spark-v1-history.jsonl.gz \
  --audit /tmp/pr215-spark-v1-fee-repair.json
```

Tests cover all three real fee receipts, decimal handling, actual withdrawal
cash, ordinary deposit exclusions, altered mint amounts or transaction hashes,
boundary aliases, pinned-balance discrepancies, and unchanged debt. The funded
replay is required before attributing any August borrowing-cost improvement to
this fix.

Validation: 1,909 unit tests pass; one existing optional-dependency test is skipped.
The compact audit and input fingerprints are in
`reconciliation/spark_ethereum_morpho_v1_fees_2026_08.json`.
