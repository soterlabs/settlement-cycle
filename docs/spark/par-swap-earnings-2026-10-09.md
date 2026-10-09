# Recognize proved stablecoin swap gains as earned funding

Spark's configured PYUSD/USDS Curve pool and three par-stable Uniswap V4 pools
sometimes return more par-valued stablecoins than the ALM pays. That difference
is earned funding, not another Sky draw or an unidentified capital receipt.

The independent audit authenticates each pool's token identities, swap amounts,
and actual ALM/pool token-transfer deltas. V4's signed amounts are the caller's
deltas, which have the opposite sign convention to V3. The poolId is recomputed
from each configured PoolKey. Curve's coin identities were independently read
at the August closing pin, Ethereum block 25878704.

Of 9,065 candidate transactions:

- 8,550 reconcile exactly between swap events and actual cash.
- 1,871 contain material gains totaling **145,059.266374643851 USD**.
- 6,679 contain execution shortfalls totaling **302,375.882078606954 USD**.
- 512 with liquidity modifications and three with other cash differences are
  excluded from this classification.

These are historical amounts through August 2026, not August revenue or an
estimate of borrowing-cost changes. The negative outcomes remain identifiable
execution costs; they are not assumed to have been entirely Sky-funded.

The historical tracing adapter labels only the proven gain as earned. It
preserves every asset movement, external-funding operation, Sky debt change and
unrelated income component. It also handles proceeds spent in the same
transaction: 554 of these gains have a zero ending cash delta in their receiving
token. A zero ending balance does not imply that no income was received.

The public evidence alone reproduces both the gains and the complete receiving
ALM cash deltas in `config/capital-tracing/spark-par-swap-gains.json`:

```
PYTHONPATH=src .venv/bin/python scripts/audit_spark_par_swaps.py \
  --evidence tests/fixtures/spark_par_swap_events.json.gz \
  --output reconciliation/spark_par_swaps_2026_08.json \
  --write-rules config/capital-tracing/spark-par-swap-gains.json
```

Registry entries require exact transaction/block/time and unchanged normalized
cash. They are applied once, and only to Spark's ALM. Tests regenerate all
1,871 entries from raw evidence, reject conflicting logs and pool keys, leave
missing-cash transactions unclassified, and verify that a gain can repay debt
without creating any new borrowed principal.

On the combined preserved history, the adapters remain idempotent and all
daily Sky debt totals remain identical. Material historical receipt residuals
fall from 12,204 to 10,472; their total falls from 74,694,983.401345 to
74,549,924.169716 USD. Other transaction components remain visible rather than
being forced to balance. The complete financing replay determines the actual
August cost attribution effect.

This is capital-tracing classification only. Published revenue, API data,
global borrowing costs and settlement reports are unchanged.
