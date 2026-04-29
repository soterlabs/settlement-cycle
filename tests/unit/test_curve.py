"""Unit tests for the Curve liquidity-event decoder."""

from __future__ import annotations

from settle.extract.curve import (
    TOPIC_ADD_LIQUIDITY,
    TOPIC_REMOVE_LIQUIDITY,
    TOPIC_REMOVE_LIQUIDITY_IMBALANCE,
    TOPIC_REMOVE_LIQUIDITY_ONE,
    _decode_curve_event,
)


def _hex_word(n: int) -> str:
    return hex(n)[2:].rjust(64, "0")


def _make_log(topic0: str, *data_words: int, block: int = 1000, log_index: int = 0):
    """Synthesize an eth_getLogs response shape for a Curve event."""
    return {
        "blockNumber": hex(block),
        "transactionHash": "0xabcd",
        "logIndex": hex(log_index),
        "topics": [topic0, "0x" + _hex_word(0xa11)],   # provider (indexed)
        "data": "0x" + "".join(_hex_word(w) for w in data_words),
    }


def test_decode_add_liquidity_2pool_signs_positive():
    """AddLiquidity(provider, [a0, a1], [f0, f1], invariant, supply) → +amounts."""
    log = _make_log(
        TOPIC_ADD_LIQUIDITY,
        1_000_000, 2_000_000,    # token_amounts[2]
        1_000, 2_000,            # fees[2]
        10**18, 10**18,          # invariant, token_supply
    )
    ev = _decode_curve_event(log, n_coins_in_pool=2)
    assert ev.amount0 == 1_000_000
    assert ev.amount1 == 2_000_000
    assert ev.is_increase is True


def test_decode_remove_liquidity_signs_negative():
    """RemoveLiquidity → negative signed amounts (nets out the prior deposit)."""
    log = _make_log(
        TOPIC_REMOVE_LIQUIDITY,
        500_000, 750_000,        # token_amounts[2]
        100, 100,                # fees[2]
        10**18,                  # token_supply
    )
    ev = _decode_curve_event(log, n_coins_in_pool=2)
    assert ev.amount0 == -500_000
    assert ev.amount1 == -750_000
    assert ev.is_increase is False


def test_decode_remove_liquidity_imbalance():
    """RemoveLiquidityImbalance has the same shape as Add but signed negative."""
    log = _make_log(
        TOPIC_REMOVE_LIQUIDITY_IMBALANCE,
        300_000, 0,
        50, 0,
        10**18, 10**18,
    )
    ev = _decode_curve_event(log, n_coins_in_pool=2)
    assert ev.amount0 == -300_000
    assert ev.amount1 == 0


def test_decode_remove_liquidity_one_assigns_to_amount0():
    """RemoveLiquidityOne emits (token_amount, coin_amount, supply). The
    decoder treats par-stable 2-pools symmetrically — the single-coin
    payout is folded into ``amount0`` (negative) since both legs price at $1.
    """
    log = _make_log(
        TOPIC_REMOVE_LIQUIDITY_ONE,
        10**18,                  # LP shares burned (data[0])
        750_000,                 # coin_amount (data[1])
        10**18,                  # token_supply (data[2])
    )
    ev = _decode_curve_event(log)
    assert ev.amount0 == -750_000
    assert ev.amount1 == 0
    assert ev.is_increase is False
