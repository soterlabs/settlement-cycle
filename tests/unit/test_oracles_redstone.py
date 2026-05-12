"""Unit tests for `settle.extract.oracles.redstone`."""

from __future__ import annotations

from decimal import Decimal

import pytest

from settle.domain import Address, Chain
from settle.extract.oracles import redstone


def _addr(seed: str) -> Address:
    return Address.from_str("0x" + seed.ljust(40, "0"))


def _pad32(value: int) -> str:
    return value.to_bytes(32, "big").hex()


def _latest_round_data(answer: int, *, round_id: int = 1, ts: int = 1_700_000_000) -> str:
    """Construct a 5-field Chainlink-AggregatorV3 ``latestRoundData()`` return."""
    return "0x" + _pad32(round_id) + _pad32(answer) + _pad32(ts) + _pad32(ts) + _pad32(round_id)


def test_read_combines_latest_round_data_and_decimals(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """answer=1017_65000000 + decimals=8 → Decimal('1017.65')."""
    answer = int(Decimal("1017.65") * 10**8)

    def _stub(chain, contract, data, block):
        if data == redstone.SEL_DECIMALS:
            return hex(8)
        if data == redstone.SEL_LATEST_ROUND_DATA:
            return _latest_round_data(answer)
        raise AssertionError(f"unexpected selector {data!r}")

    monkeypatch.setattr(redstone, "eth_call", _stub)
    assert redstone.read(Chain.ETHEREUM, _addr("aa"), 25_000_000) == Decimal("1017.65")


def test_read_handles_18_decimal_feeds(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """Same shape as Chronicle's 18-decimal scale should also work."""
    answer = int(Decimal("1.123456") * 10**18)

    def _stub(chain, contract, data, block):
        return hex(18) if data == redstone.SEL_DECIMALS else _latest_round_data(answer)

    monkeypatch.setattr(redstone, "eth_call", _stub)
    assert redstone.read(Chain.ETHEREUM, _addr("aa"), 25_000_000) == Decimal("1.123456")


def test_read_wraps_rpc_revert(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """RPC revert surfaces as RedstoneReadError so the dispatcher falls through."""
    from settle.extract.rpc import RPCError

    def _revert(*args, **kwargs):
        raise RPCError("execution reverted")

    monkeypatch.setattr(redstone, "eth_call", _revert)
    with pytest.raises(redstone.RedstoneReadError, match="reverted"):
        redstone.read(Chain.ETHEREUM, _addr("bb"), 12345)


def test_read_raises_on_empty_pre_deployment(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """``0x`` from either call → RedstoneReadError (pre-deployment / uninitialised)."""
    monkeypatch.setattr(redstone, "eth_call", lambda *a, **k: "0x")
    with pytest.raises(redstone.RedstoneReadError, match="empty"):
        redstone.read(Chain.ETHEREUM, _addr("cc"), 24_000_000)


def test_read_caches_by_chain_oracle_block(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """Repeat read at the same key hits the cache; fresh block re-invokes."""
    n_calls = {"value": 0}
    answer = int(Decimal("1017.65") * 10**8)

    def _stub(chain, contract, data, block):
        n_calls["value"] += 1
        return hex(8) if data == redstone.SEL_DECIMALS else _latest_round_data(answer)

    monkeypatch.setattr(redstone, "eth_call", _stub)

    addr = _addr("dd")
    redstone.read(Chain.ETHEREUM, addr, 25_000_000)
    redstone.read(Chain.ETHEREUM, addr, 25_000_000)
    assert n_calls["value"] == 2, "first read = 2 inner eth_calls; second hits redstone.read cache"

    redstone.read(Chain.ETHEREUM, addr, 25_000_001)
    assert n_calls["value"] == 4, "fresh block re-invokes both inner calls"
