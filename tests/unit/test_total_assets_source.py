"""Venue.total_assets_source — validation + S2 HyperSync reconstruction."""

from __future__ import annotations

import pytest

from settle.domain.config import _parse_total_assets_source


def test_default_is_rpc():
    assert _parse_total_assets_source({"id": "S1", "pricing_category": "A"}) == "rpc"


def test_hypersync_underlying_requires_s2():
    with pytest.raises(ValueError, match=r"only\s+supported on pricing_category S2"):
        _parse_total_assets_source({
            "id": "S1", "pricing_category": "B",
            "total_assets_source": "hypersync_underlying",
        })


def test_invalid_value_raises():
    with pytest.raises(ValueError, match="invalid total_assets_source"):
        _parse_total_assets_source({
            "id": "S61", "pricing_category": "S2",
            "total_assets_source": "hypersync",
        })


def test_s2_hypersync_underlying_ok():
    assert _parse_total_assets_source({
        "id": "S61", "pricing_category": "S2",
        "total_assets_source": "hypersync_underlying",
    }) == "hypersync_underlying"


def test_erc20_balance_from_transfers_sums_and_dedupes():
    from settle.normalize.sources.hypersync_position_balance import (
        erc20_balance_from_transfers,
    )

    holder = bytes.fromhex("de770c84fe66e063336b31737cfe9790f18c4087")
    token = bytes.fromhex("5fc5360d0400a0fd4f2af552add042d716f1d168")
    ht = "0x" + "00" * 12 + holder.hex()
    other = "0x" + "00" * 12 + "ab" * 20

    class Row:
        def __init__(self, bn, li, t1, t2, data):
            self.block_number, self.log_index = bn, li
            self.topic1, self.topic2, self.data = t1, t2, data

    rows = [
        Row(1, 0, other, ht, hex(100)),   # inflow +100
        Row(2, 0, ht, other, hex(30)),    # outflow -30
        Row(3, 0, ht, ht, hex(7)),        # self-transfer nets 0
        Row(3, 0, ht, ht, hex(7)),        # duplicate (both selections) — deduped
    ]
    bal = erc20_balance_from_transfers(
        "robinhood", token, holder, 10, fetch_logs=lambda *a, **k: rows,
    )
    assert bal == 70
