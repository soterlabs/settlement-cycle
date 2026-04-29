"""Unit tests for `settle.extract.oracles.chronicle`."""

from __future__ import annotations

from decimal import Decimal

import pytest

from settle.domain import Address, Chain
from settle.extract.oracles import chronicle


def _addr(seed: str) -> Address:
    return Address.from_str("0x" + seed.ljust(40, "0"))


def test_read_returns_decimal_scaled_by_1e18(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """Chronicle Scribe returns ``uint256`` scaled to 1e18. A raw value of
    ``1.10e18`` should come back as ``Decimal('1.10')``."""
    raw_value = int(Decimal("1.10") * 10**18)
    monkeypatch.setattr(
        chronicle, "eth_call",
        lambda chain, contract, data, block: hex(raw_value),
    )
    out = chronicle.read(Chain.ETHEREUM, _addr("aa"), 12345)
    assert out == Decimal("1.10")


def test_read_wraps_rpc_revert_as_chronicle_error(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """RPC reverts (e.g. caller not on the kiss/allowlist) surface as
    ``ChronicleReadError`` so the price-dispatch layer can fall back."""
    from settle.extract.rpc import RPCError

    def _revert(*args, **kwargs):
        raise RPCError("execution reverted")

    monkeypatch.setattr(chronicle, "eth_call", _revert)
    with pytest.raises(chronicle.ChronicleReadError, match="reverted"):
        chronicle.read(Chain.ETHEREUM, _addr("bb"), 12345)


def test_read_caches_by_chain_oracle_block(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """Hitting the same (chain, oracle, block) hits the cache the second time."""
    n_calls = {"value": 0}
    def _stub(chain, contract, data, block):
        n_calls["value"] += 1
        return hex(int(Decimal("1.05") * 10**18))
    monkeypatch.setattr(chronicle, "eth_call", _stub)

    addr = _addr("cc")
    chronicle.read(Chain.ETHEREUM, addr, 24971074)
    chronicle.read(Chain.ETHEREUM, addr, 24971074)
    assert n_calls["value"] == 1, "second call must hit the cache"

    chronicle.read(Chain.ETHEREUM, addr, 24971075)
    assert n_calls["value"] == 2, "different block must miss the cache"
