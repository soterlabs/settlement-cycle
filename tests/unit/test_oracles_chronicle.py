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


def _encode_read_with_age(value: int, age: int) -> str:
    """ABI-encode the 2-word ``readWithAge()`` return (value, age)."""
    return "0x" + value.to_bytes(32, "big").hex() + age.to_bytes(32, "big").hex()


def test_read_caches_by_chain_oracle_block(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """Hitting the same (chain, oracle, block) hits the cache the second time."""
    n_calls = {"value": 0}
    def _stub(chain, contract, data, block):
        n_calls["value"] += 1
        assert data == chronicle.SEL_READ_WITH_AGE
        return _encode_read_with_age(int(Decimal("1.05") * 10**18), 1_000_000)
    monkeypatch.setattr(chronicle, "eth_call", _stub)
    monkeypatch.setattr(chronicle, "block_timestamp", lambda chain, block: 1_000_100)

    addr = _addr("cc")
    chronicle.read(Chain.ETHEREUM, addr, 24971074)
    chronicle.read(Chain.ETHEREUM, addr, 24971074)
    assert n_calls["value"] == 1, "second call must hit the cache"

    chronicle.read(Chain.ETHEREUM, addr, 24971075)
    assert n_calls["value"] == 2, "different block must miss the cache"


def test_read_falls_back_to_plain_read_without_read_with_age(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """A contract without ``readWithAge()`` (empty/short return) still reads
    via plain ``read()`` — the pre-2026-08 behaviour."""
    def _stub(chain, contract, data, block):
        if data == chronicle.SEL_READ_WITH_AGE:
            return "0x"                       # unsupported selector
        assert data == chronicle.SEL_READ
        return hex(int(Decimal("1.07") * 10**18))
    monkeypatch.setattr(chronicle, "eth_call", _stub)

    out = chronicle.read(Chain.ETHEREUM, _addr("dd"), 12345)
    assert out == Decimal("1.07")


def test_read_warns_on_stale_feed(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch, caplog,
):
    """A feed whose last update is older than ``STALE_WARN_SECONDS`` at the
    queried block logs a WARNING (but still returns the value — staleness
    is a tripwire, not a failure). Guards the E22 Consumer_2 incident:
    a rotated-out VAO consumer kept answering with a frozen value and NAV
    silently pinned for two settlement months."""
    import logging

    frozen_age = 1_000_000
    block_ts = frozen_age + chronicle.STALE_WARN_SECONDS + 86400  # 15 days later
    monkeypatch.setattr(
        chronicle, "eth_call",
        lambda chain, contract, data, block:
            _encode_read_with_age(int(Decimal("1.016057") * 10**18), frozen_age),
    )
    monkeypatch.setattr(chronicle, "block_timestamp", lambda chain, block: block_ts)

    with caplog.at_level(logging.WARNING, logger="settle.extract.oracles.chronicle"):
        out = chronicle.read(Chain.ETHEREUM, _addr("ee"), 25218797)
    assert out == Decimal("1.016057")
    assert any("STALE" in r.message for r in caplog.records)


def test_read_no_warning_on_fresh_feed(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch, caplog,
):
    """A feed updated within the threshold logs nothing."""
    import logging

    age = 2_000_000
    monkeypatch.setattr(
        chronicle, "eth_call",
        lambda chain, contract, data, block:
            _encode_read_with_age(int(Decimal("1.02") * 10**18), age),
    )
    monkeypatch.setattr(chronicle, "block_timestamp", lambda chain, block: age + 3600)

    with caplog.at_level(logging.WARNING, logger="settle.extract.oracles.chronicle"):
        out = chronicle.read(Chain.ETHEREUM, _addr("ff"), 25218797)
    assert out == Decimal("1.02")
    assert not caplog.records
