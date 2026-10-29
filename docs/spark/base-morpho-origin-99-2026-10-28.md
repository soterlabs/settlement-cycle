# Spark Base Morpho USDC: August origin coverage above 99%

At August 31, S34 has **$283,116,139.25** acquisition capital. We now trace
**$280,456,335.51 (99.0605%)** to original Sky borrowing, leaving
**$2,659,803.74** unclassified. The previous reviewed result was 93.4710%.
Capital, production revenues and published settlements are unchanged.

This measures the capital still held at month-end. It is not average monthly
capital, current outstanding debt, or Prime-wide borrowing-cost reconciliation.
Unknown capital can include gains and other funding; it is not assumed debt.

## What explains the improvement

The scoped ledger now includes the Ethereum and L2 USDC positions that fund
S34, their authenticated CCTP claims, mainnet idle USDS, and configured PSM3
share accounts. USDC can return from another chain, repay some Sky debt, and
bridge to Base again. PSM3 shares can also be redeemed into a Morpho deposit.
Neither route is a new draw at the final allocation, but both can carry
previously proven borrowed acquisition basis.

The graph checks each selected message's source and, when received by the
cutoff, its unique matching full receipt, amount, chain and temporal order.
Ownership comes from the existing normalized message and PSM3 extractors,
not from a heuristic amount/date match. A pending message retains its separate
claim; it is not also counted as a deployed allocation.

153 receipt proofs also distinguish a gross draw that funds a bridge from an
unrelated later wipe. For example,
[0x0c75…51eb](https://etherscan.io/tx/0x0c75c1ed6074e317d0fcc0d6e691a6abd0393c148cb33081bf7a2a9e698051eb)
draws **594,394.832629 USDS**, converts it through DAI/PSM to USDC, and funds
CCTP claim `0:494564`. A later **550,009.772338328275033 USDS** wipe leaves
only about $44k of net new debt. The bridge still received the full original
draw, and the already rate-normalized Draw/Wipe/Vat data verifies that route.

A Base PSM3 withdrawal of **970,045.133082 USDC** in
[0x366f…7950](https://basescan.org/tx/0x366fb11a73536ac336ad92f02c7d3d61f6d51443878fc147db7329a4246f7950)
was another previously unclassified source. The graph now follows its earlier
PSM3 acquisition funding rather than treating its redemption as a fresh draw.

## Controls

- Each CCTP proof matches the normalized claim to its nonce/emitter, USDC token,
  ALM owner, unconsumed TokenMinter payment and actual burn.
- A receipt bound applies only when it covers **all** positive scoped accounts
  in that transaction. Other destinations need their own complete proof.
- The receipt is an alternative lower bound for the same inflows. It replaces
  a weaker bound; it is never added on top of existing carried/drawn funding.
- Receipt-proven amounts stay attached to their individual messages; an
  aggregate proof is not spread over messages with different funding shares.
- Other group transfers retain the weighted-average funding convention. Withdrawals carry
  acquisition basis and do not relabel passive appreciation as borrowing.
- Production code does not call these research scripts.

## Reproduction

```sh
PYTHONPATH=src:scripts .venv/bin/python scripts/audit_spark_usdc_origin_graph.py \
  --normalized-history tests/fixtures/spark_base_morpho_origin_history.jsonl.gz \
  --supplement tests/fixtures/spark_usdc_origin_graph_supplement.jsonl.gz \
  --receipts tests/fixtures/spark_usdc_cctp_draw_receipts.json.gz \
  --output reconciliation/spark_base_morpho_graph_origins_2026_08.json.gz
```

The supplemental fixture adds 105,639 relevant complete batches to the existing
316,812-batch fixture. The shared batches were checked against the full adapted
history; conflicting identities are rejected. This fixture replay reproduces
the full-history result, including unchanged capital. The artifact retains
monthly results, residual contributions, message links, receipt witnesses and
input/code hashes. Repeated unchanged target snapshots are omitted from its
compact trace; all upstream calculations remain reproducible from the inputs.

Tests mutate nonce, owner, amounts, burns and duplicate logs; check group-bound
coverage and no double counting; reject inconsistent supplemental history; and
reproduce August's result above 99%. The shared USDC route helper preserves the
previously tested Anchorage result.
