# Reserve-factor receipts in Spark's older capital snapshot

The immutable August tracing snapshot labels **84 direct reserve-treasury
BalanceTransfer legs in 21 transactions** with zero `external_income`.
Their execution-index value is **3,178,617.573891478336382678334 USD** from
September 2025 through August 2026. They are earned reserve-factor funding,
not additional Sky-borrowed capital.

The current configuration and capital normalizer already recognize both
reserve treasuries. This repair makes the older saved tracing input consistent
with that classification; it is not a claim that a newly extracted current
history still has the same defect. No published revenue or September true-up
is regenerated, and settlement recognition start dates are unchanged.

The [January 15, 2026 spell](https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20260115/SparkEthereum_20260115.sol)
provides a concrete failure case. Its January 19 execution both collects
reserve-factor income and draws 350m USDS for Optimism/Arbitrum bridge funding.
The saved history has 118,566.1605729346853802752871 spDAI and
68,663.64514512358745100448765 spUSDS receipt value but no earned-funding labels.
Generic transaction clearing therefore assigns **187,229.805718…** of the
unrelated debt draw to those receipts. Recognizing the observed treasury gifts
keeps their borrowed basis at zero and leaves the entire 350m draw available
for its actual bridge routes. Until those bridge links are complete, the raw
unmatched outflow increases to the correct 350m; forcing a smaller residual
would conceal the funding mistake.

`src/settle/compute/spark_reserve_gifts.py` contains only the exact observed
transaction/block/time/token/index-valued transfers. The fixture retains the
canonical transaction logs and corresponding original normalized batches.
Tests independently reconstruct all 84 gifts from scaled units and the emitted
liquidity index. The adapter preserves position values, net movements and every
per-ilk debt record. Fresh inputs with the correct income value are unchanged;
conflicting income, metadata or insufficient receipts fail closed.

One mixed transaction (`0xd157dbc5…d239d`) contains additional spDAI/spUSDS
receipts from another sender. Only its directly proven treasury legs are
recognized here. The remaining ingress is not automatically classified as
income. Tests explicitly retain that distinction.

This is a capital-funding diagnostic repair. It neither increases historical
published revenue nor adds a second settlement of the reserve-factor true-up.
It does not resolve Spark Savings V2 funding or certify full reconciliation.
