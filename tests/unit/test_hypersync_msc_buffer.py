"""Unit tests for the HyperSync MSC-buffer source — decode helpers, the
month-range utility, and the mock-transport stream extraction."""

from __future__ import annotations

from decimal import Decimal

import pytest

from settle.normalize.sources import hypersync_msc_buffer as M


# ── shared helpers (re-exported from _hypersync_common) ─────────────────────

def test_addr_topic_pads_lowercased():
    assert M._addr_topic("0xAbCd") == "0x" + "0" * 24 + "abcd"


def test_word_decodes_positional():
    data = "0x" + "00" * 31 + "05" + "00" * 31 + "07" + "ff" * 32
    assert M._word(data, 0) == 5
    assert M._word(data, 1) == 7


def test_sel_and_evt_match_known_constants():
    # These are the actual FROB / GRAB selectors used across the codebase.
    assert M._sel("grab(bytes32,address,address,address,int256,int256)") == M._GRAB
    # Transfer's full-keccak topic0 — standard ERC-20 event.
    assert M._TRANSFER.startswith("0xddf252ad")


# ── _next_month_range boundary cases ────────────────────────────────────────

class _MonthLike:
    def __init__(self, year, month):
        self.year = year
        self.month = month


def test_next_month_range_january_2026():
    start, end = M._next_month_range(_MonthLike(2026, 1))
    # Feb 1 2026 → Mar 1 2026 (UTC midnight)
    assert start == 1769904000
    assert end   == 1772323200


def test_next_month_range_november_rolls_to_january():
    start, end = M._next_month_range(_MonthLike(2026, 11))
    # Dec 1 2026 → Jan 1 2027
    assert start == 1796083200
    assert end   == 1798761600


def test_next_month_range_december_rolls_to_february():
    start, end = M._next_month_range(_MonthLike(2026, 12))
    # Jan 1 2027 → Feb 1 2027
    assert start == 1798761600
    assert end   == 1801440000


# ── settlement-block resolution: config anchor path ─────────────────────────

_TEST_CFG = {
    "allocator_ilks": {
        "spark": "0x414c4c4f4341544f522d535041524b2d41000000000000000000000000000000",
        "grove": "0x414c4c4f4341544f522d424c4f4f4d2d41000000000000000000000000000000",
        "obex":  "0x414c4c4f4341544f522d4f4245582d4100000000000000000000000000000000",
    },
    "subproxies": {
        "spark":   "0x3300f198988e4c9c63f75df86de36421f06af8c4",
        "grove":   "0x1369f7b2b38c76b6478c0f0e66d94923421891ba",
        "obex":    "0x8be042581f581e3620e29f213ea8b94afa1c8071",
        "keel":    "0x355cd90ecb1b409fdf8b64c4473c3b858da2c310",
        "skybase": "0x08978e3700859e476201c1d7438b3427e3c81140",
    },
    "demand_side_buffer": "0x5e2fec3a3c4e63a422e45c1bb83edb3a5ad0543b",
    "core_council_multisig": "0x210cfcf53d1f9648c1c4dcaee677f0cb06914364",
    "settlement_blocks": {"2026-06": 25574490},
    "grove_tge_penalty": {},
    "one_off_transfers": {},
}


def test_config_anchored_settlement_past_pin_raises():
    src = M.HyperSyncMscBufferSource(config=_TEST_CFG, post=lambda *a, **k: None)
    with pytest.raises(M.SettlementNotFoundError, match="past pin_block"):
        src.streams(_MonthLike(2026, 6), pin_block=100)


def test_auto_detect_disabled_without_env(monkeypatch):
    # No settlement_blocks entry for 2026-07 in cfg, and env var unset.
    monkeypatch.delenv("SKY_TOTAL_ALLOW_AUTODETECT", raising=False)
    src = M.HyperSyncMscBufferSource(config=_TEST_CFG, post=lambda *a, **k: None)
    with pytest.raises(M.SettlementNotFoundError, match="no settlement_block"):
        src.streams(_MonthLike(2026, 7), pin_block=99999999)
