"""Redstone (Chainlink AggregatorV3-compatible) oracle reader.

Redstone push-model adapters expose Chainlink's ``AggregatorV3Interface``:
- ``latestRoundData()`` returns ``(roundId, answer, startedAt, updatedAt, answeredInRound)``
- ``decimals()`` returns the scale of ``answer``.

Many Redstone deployments use a single-round-overwrite pattern: ``roundId``
stays at ``1`` while ``answer`` and ``updatedAt`` advance with each push.
Don't rely on ``roundId`` for staleness — use ``updatedAt`` if a freshness
check is needed (not implemented here; the dispatcher in ``normalize.prices``
treats any successful read as authoritative).

Selectors:
- ``latestRoundData()`` → 0xfeaf968c
- ``decimals()``        → 0x313ce567
"""

from __future__ import annotations

from decimal import Decimal

from ...domain.primes import Address, Chain
from ..cache import cached
from ..rpc import RPCError, eth_call

SEL_LATEST_ROUND_DATA = "0xfeaf968c"
SEL_DECIMALS = "0x313ce567"


class RedstoneReadError(RuntimeError):
    """Raised when ``latestRoundData()`` reverts or returns empty data
    (pre-deployment block, feed uninitialised, etc.)."""


@cached(source_id="redstone.read")
def read(chain: Chain, oracle: Address, block: int) -> Decimal:
    """Read the Redstone price at ``block``. Returns USD-denominated `Decimal`.

    Issues two ``eth_call``s — ``decimals()`` and ``latestRoundData()`` —
    then divides ``answer`` by ``10 ** decimals``. Both inner calls are
    independently cached by ``(chain, oracle, data, block)``.
    """
    try:
        decimals_raw = eth_call(chain, oracle, SEL_DECIMALS, block)
        round_data_raw = eth_call(chain, oracle, SEL_LATEST_ROUND_DATA, block)
    except RPCError as e:
        raise RedstoneReadError(
            f"Redstone read reverted at {oracle.hex} on {chain.value} "
            f"block {block}: {e}"
        ) from e
    if decimals_raw in ("0x", "0x0") or round_data_raw == "0x":
        raise RedstoneReadError(
            f"Redstone returned empty data at {oracle.hex} on {chain.value} "
            f"block {block} (likely pre-deployment or uninitialised)"
        )
    decimals = int(decimals_raw, 16)
    raw = bytes.fromhex(round_data_raw[2:])
    if len(raw) < 64:
        raise RedstoneReadError(
            f"Redstone latestRoundData returned {len(raw)} bytes "
            f"(expected ≥160) at {oracle.hex} block {block}"
        )
    # answer is int256 at offset 32–64; sign-aware decode (NAVs are positive
    # in practice but the ABI is signed).
    answer = int.from_bytes(raw[32:64], "big", signed=True)
    return Decimal(answer) / Decimal(10 ** decimals)
