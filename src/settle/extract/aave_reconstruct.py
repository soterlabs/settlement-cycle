"""Wei-exact reconstruction of Aave V3 / SparkLend aToken balances from events.

An aToken's ``scaledBalanceOf`` changes only on Mint/Burn/BalanceTransfer, and
``balanceOf = scaledBalance.rayMul(reserveNormalizedIncome)``. Both inputs are
fully derivable from logs:

  * scaled balance ← Mint/Burn (scaled Δ = ``rayDiv(amount, index)``) +
    BalanceTransfer (whose ``value`` is already the scaled amount);
  * reserveNormalizedIncome ← the reserve's last ``ReserveDataUpdated``
    (``liquidityIndex``, ``liquidityRate``) linearly accrued to the query
    block: ``index.rayMul(RAY + rate·Δt / SECONDS_PER_YEAR)`` — Aave's
    ``MathUtils.calculateLinearInterest`` / ``ReserveLogic.getNormalizedIncome``.

Replicates Aave's ``WadRayMath`` (half-up rounding) exactly. Validated live vs
RPC ``scaledBalanceOf`` / ``balanceOf`` to the wei (SparkLend spUSDS). Pure
functions — ``fetch_logs`` and ``block_ts`` are injected (store + HyperSync in
prod, stubs in tests).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

RAY = 10**27
SECONDS_PER_YEAR = 365 * 86400

# Aave V3 aToken / SparkLend spToken event topics.
MINT_T0 = "0x458f5fa412d0f69b08dd84872b0215675cc67bc1d5b6fd93300a1c3878b86196"
BURN_T0 = "0x4cf25bc1d991c17529c25213d3cc0cda295eeaad5f13f361969b12ea48015f90"
BT_T0 = "0x4beccb90f994c31aced7a23b5611020728a23d8ec5cddd1a3e9d97b96fda8666"
# Pool event.
RDU_T0 = "0x804c9b842b2748a22bb64b345453a3de7ca54a6ca45ce00d415894979e22897a"


def ray_div(a: int, b: int) -> int:
    return (a * RAY + b // 2) // b


def ray_mul(a: int, b: int) -> int:
    return (a * b + RAY // 2) // RAY


def _addr_topic(addr: bytes) -> str:
    return "0x" + bytes(addr).hex().rjust(64, "0")


def _words(data: str) -> list[int]:
    h = data[2:] if data.startswith("0x") else data
    return [int(h[i : i + 64], 16) for i in range(0, len(h), 64)]


def scaled_balance_at(
    chain: str, atoken: bytes, holder: bytes, block: int, *,
    fetch_logs: Callable[..., list[Any]],
) -> int:
    """``scaledBalanceOf(holder)`` at ``block`` from aToken events (exact)."""
    ht = _addr_topic(holder)
    tok = "0x" + bytes(atoken).hex()
    sel = [
        {"address": [tok], "topics": [[MINT_T0], [], [ht]]},   # Mint onBehalfOf == holder
        {"address": [tok], "topics": [[BURN_T0], [ht]]},       # Burn from == holder
        {"address": [tok], "topics": [[BT_T0], [ht]]},         # BalanceTransfer from == holder
        {"address": [tok], "topics": [[BT_T0], [], [ht]]},     # BalanceTransfer to == holder
    ]
    rows = sorted(fetch_logs(chain, sel, 0, block), key=lambda r: (r.block_number, r.log_index))
    scaled = 0
    seen: set[tuple[int, int]] = set()
    for r in rows:
        k = (r.block_number, r.log_index)
        if k in seen:
            continue
        seen.add(k)
        w = _words(r.data)
        if r.topic0 == MINT_T0:                      # value, balanceIncrease, index
            scaled += ray_div(w[0] - w[1], w[2])     # amount = value − balanceIncrease
        elif r.topic0 == BURN_T0:
            scaled -= ray_div(w[0] + w[1], w[2])     # amount = value + balanceIncrease
        elif r.topic0 == BT_T0:                      # value (already scaled), index
            if r.topic1 == ht:
                scaled -= w[0]
            if r.topic2 == ht:
                scaled += w[0]
    return scaled


def normalized_income(
    chain: str, pool: bytes, reserve: bytes, block: int, *,
    fetch_logs: Callable[..., list[Any]],
    block_ts: Callable[[str, int], int],
) -> int:
    """``getReserveNormalizedIncome(reserve)`` at ``block`` (ray), from the
    pool's last ``ReserveDataUpdated`` linearly accrued to the block."""
    sel = [{"address": ["0x" + bytes(pool).hex()],
            "topics": [[RDU_T0], [_addr_topic(reserve)]]}]
    rows = sorted(fetch_logs(chain, sel, 0, block), key=lambda r: (r.block_number, r.log_index))
    if not rows:
        raise ValueError(f"no ReserveDataUpdated for reserve {reserve.hex()} <= block {block}")
    last = rows[-1]
    w = _words(last.data)                             # liquidityRate, _, _, liquidityIndex, _
    liquidity_rate, liquidity_index = w[0], w[3]
    dt = block_ts(chain, block) - block_ts(chain, last.block_number)
    if dt <= 0:
        return liquidity_index
    linear = RAY + (liquidity_rate * dt) // SECONDS_PER_YEAR
    return ray_mul(liquidity_index, linear)


def rebased_balance_at(
    chain: str, atoken: bytes, holder: bytes, block: int, *,
    pool: bytes, reserve: bytes,
    fetch_logs: Callable[..., list[Any]],
    block_ts: Callable[[str, int], int],
) -> int:
    """Rebased ``balanceOf(holder)`` = scaledBalance × normalizedIncome (exact)."""
    scaled = scaled_balance_at(chain, atoken, holder, block, fetch_logs=fetch_logs)
    ni = normalized_income(chain, pool, reserve, block, fetch_logs=fetch_logs, block_ts=block_ts)
    return ray_mul(scaled, ni)
