"""Unit tests for ``compute.monthly_pnl._build_paired_principal_caps``.

The auto-wiring loop iterates a prime's display_only venues, applies a
chain of filters (display_only, skip, paired_with, paired_source +
holder_override present, chain match, prime has ALM + period has
pin_block), and fetches each surviving venue's cumulative ALM→holder
outflow series via ``directed_inflow_timeseries``. Multiple display-only
venues sharing the same ``paired_source`` are merged via
``_merge_cap_series``.

These tests cover each filter independently and the merge path. The
real Grove + April fixture is exercised end-to-end by the acceptance
runner scripts; this file covers the unit-level wiring contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd

from settle.compute.monthly_pnl import _build_paired_principal_caps
from settle.domain import Chain
from settle.domain.primes import Address


def _b20(hex_str: str) -> bytes:
    """20-byte address from hex (with or without 0x prefix)."""
    return bytes.fromhex(hex_str.removeprefix("0x")).rjust(20, b"\x00")


def _addr(hex_str: str) -> Address:
    return Address(_b20(hex_str))


# ----------------------------------------------------------------------------
# Lightweight stand-ins for the Venue / Prime / Period dataclasses. The real
# types are heavy and immutable; we only need the attributes the helper
# touches.
# ----------------------------------------------------------------------------

@dataclass
class _TokRef:
    address: Address


@dataclass
class _Venue:
    id: str
    chain: Chain
    display_only: bool = False
    skip: bool = False
    paired_with: str | None = None
    paired_source: Address | None = None
    holder_override: Address | None = None
    token: _TokRef = field(
        default_factory=lambda: _TokRef(_addr("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"))
    )


@dataclass
class _Prime:
    venues: list[_Venue]
    alm: dict[Chain, Address]
    start_date: date = date(2025, 10, 23)


@dataclass
class _Period:
    pin_blocks: dict[Chain, int]


class _RecordingBalanceSource:
    """Stub balance source: records each ``directed_inflow_timeseries`` call
    and returns a programmable DataFrame per (token, from_addr, to_addr).
    """

    def __init__(self, registry: dict[tuple[bytes, bytes, bytes], pd.DataFrame]):
        self.registry = registry
        self.calls: list[dict[str, Any]] = []

    def directed_inflow_timeseries(self, **kwargs):
        self.calls.append(kwargs)
        key = (
            kwargs["token"] if isinstance(kwargs["token"], bytes) else bytes(kwargs["token"]),
            kwargs["from_addr"] if isinstance(kwargs["from_addr"], bytes) else bytes(kwargs["from_addr"]),
            kwargs["to_addr"] if isinstance(kwargs["to_addr"], bytes) else bytes(kwargs["to_addr"]),
        )
        return self.registry.get(
            key,
            pd.DataFrame({"block_date": [], "daily_inflow": [], "cum_inflow": []}),
        )


# Standard test fixtures: an anchor Cat A venue on Ethereum, a display_only
# EOA paired to it, an ALM, and a token.
ANCHOR_ID = "E_ANCHOR"
PAIRED_SOURCE = _addr("0x94b398acb2fce988871218221ea6a4a2b26cccbc")
ALM_ETH = _addr("0x491edfb0b8b608044e227225c715981a30f3a44e")
EOA_RELAY = _addr("0xd94f9ef3395bbe41c1f05ced3c9a7dc520d08036")
USDC = _addr("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")


def _anchor_venue() -> _Venue:
    return _Venue(id=ANCHOR_ID, chain=Chain.ETHEREUM)


def _eoa_pair(
    vid: str = "E_EOA",
    *,
    display_only: bool = True,
    skip: bool = False,
    paired_with: str | None = ANCHOR_ID,
    chain: Chain = Chain.ETHEREUM,
    paired_source: Address | None = PAIRED_SOURCE,
    holder_override: Address | None = EOA_RELAY,
) -> _Venue:
    return _Venue(
        id=vid, chain=chain,
        display_only=display_only, skip=skip,
        paired_with=paired_with, paired_source=paired_source,
        holder_override=holder_override,
    )


def _period(pin: int = 24996367) -> _Period:
    return _Period(pin_blocks={Chain.ETHEREUM: pin})


def _outflow_df(rows: list[tuple[date, Decimal]]) -> pd.DataFrame:
    """Build a cumulative-outflow DataFrame with the expected schema."""
    return pd.DataFrame([
        {"block_date": d, "cum_inflow": v} for d, v in rows
    ])


# ----------------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------------

def test_happy_path_single_paired_eoa_registers_cap():
    """The canonical Grove case: anchor + one display_only EOA paired to it.
    The helper should fetch directed_inflow_timeseries(ALM → relay) and
    register it keyed by paired_source."""
    anchor = _anchor_venue()
    eoa = _eoa_pair()
    prime = _Prime(venues=[anchor, eoa], alm={Chain.ETHEREUM: ALM_ETH})
    period = _period()

    cap_df = _outflow_df([
        (date(2025, 11, 24), Decimal("25000000")),
        (date(2025, 12, 16), Decimal("50000000")),
    ])
    src = _RecordingBalanceSource(
        {(USDC.value, ALM_ETH.value, EOA_RELAY.value): cap_df}
    )

    out = _build_paired_principal_caps(prime, anchor, period, src)

    assert PAIRED_SOURCE.value in out
    pd.testing.assert_frame_equal(out[PAIRED_SOURCE.value], cap_df)
    assert len(src.calls) == 1
    call = src.calls[0]
    assert call["chain"] == "ethereum"
    assert call["from_addr"] == ALM_ETH.value
    assert call["to_addr"] == EOA_RELAY.value
    assert call["pin_block"] == 24996367


def test_skips_non_display_only_venue():
    """A regular (non-display) Cat A venue that happens to set paired_with
    should NOT contribute to the cap — only display_only venues drive it."""
    anchor = _anchor_venue()
    not_display = _eoa_pair(display_only=False)
    prime = _Prime(venues=[anchor, not_display], alm={Chain.ETHEREUM: ALM_ETH})
    period = _period()
    src = _RecordingBalanceSource({})

    out = _build_paired_principal_caps(prime, anchor, period, src)

    assert out == {}
    assert src.calls == []


def test_skipped_venue_is_excluded_from_cap():
    """A display_only venue marked ``skip=True`` should be excluded even
    though it's still in prime.venues for reporting."""
    anchor = _anchor_venue()
    eoa = _eoa_pair(skip=True)
    prime = _Prime(venues=[anchor, eoa], alm={Chain.ETHEREUM: ALM_ETH})
    period = _period()
    src = _RecordingBalanceSource({(USDC.value, ALM_ETH.value, EOA_RELAY.value):
                                   _outflow_df([(date(2025, 11, 1), Decimal("1"))])})

    out = _build_paired_principal_caps(prime, anchor, period, src)

    assert out == {}
    assert src.calls == []


def test_paired_with_mismatch_is_ignored():
    """An EOA paired to a different anchor venue should not affect this anchor."""
    anchor = _anchor_venue()
    eoa = _eoa_pair(paired_with="SOME_OTHER_ANCHOR")
    prime = _Prime(venues=[anchor, eoa], alm={Chain.ETHEREUM: ALM_ETH})
    period = _period()
    src = _RecordingBalanceSource({})

    out = _build_paired_principal_caps(prime, anchor, period, src)
    assert out == {}


def test_missing_paired_source_or_holder_override_skips():
    """An EOA without paired_source OR holder_override has no usable cap target;
    the helper skips silently."""
    anchor = _anchor_venue()
    eoa1 = _eoa_pair(vid="E1", paired_source=None)
    eoa2 = _eoa_pair(vid="E2", holder_override=None)
    prime = _Prime(venues=[anchor, eoa1, eoa2], alm={Chain.ETHEREUM: ALM_ETH})
    period = _period()
    src = _RecordingBalanceSource({})

    out = _build_paired_principal_caps(prime, anchor, period, src)
    assert out == {}


def test_chain_mismatch_with_anchor_is_ignored():
    """Cross-chain paired-cap is not supported; EOA on a different chain than
    the anchor is filtered out silently."""
    anchor = _anchor_venue()  # ethereum
    eoa = _eoa_pair(chain=Chain.BASE)
    prime = _Prime(venues=[anchor, eoa], alm={
        Chain.ETHEREUM: ALM_ETH, Chain.BASE: _addr("0x9b746dbc5269e1df6e4193bcb441c0fbbf1cecee"),
    })
    period = _period()
    src = _RecordingBalanceSource({})

    out = _build_paired_principal_caps(prime, anchor, period, src)
    assert out == {}


def test_missing_alm_or_pin_block_warns_and_skips(caplog):
    """If the EOA's chain has no prime.alm entry OR no period.pin_block,
    helper skips with a warning rather than crashing."""
    anchor = _anchor_venue()
    eoa = _eoa_pair(chain=Chain.MONAD)
    # MONAD missing from BOTH prime.alm and period.pin_blocks.
    prime = _Prime(venues=[anchor, eoa], alm={Chain.ETHEREUM: ALM_ETH})
    period = _period()
    src = _RecordingBalanceSource({})

    import logging
    with caplog.at_level(logging.WARNING, logger="settle.compute.monthly_pnl"):
        out = _build_paired_principal_caps(prime, anchor, period, src)

    # Different chain than the anchor — filtered upstream of the
    # alm/pin_block check.
    assert out == {}


def test_two_display_only_eoas_sharing_paired_source_get_merged():
    """When two display_only EOAs paired to the same anchor share the
    same paired_source, their cap series are SUMMED via _merge_cap_series."""
    anchor = _anchor_venue()
    eoa1 = _eoa_pair(vid="E_A", holder_override=_addr("0xaaaa00000000000000000000000000000000aaaa"))
    eoa2 = _eoa_pair(vid="E_B", holder_override=_addr("0xbbbb00000000000000000000000000000000bbbb"))
    prime = _Prime(venues=[anchor, eoa1, eoa2], alm={Chain.ETHEREUM: ALM_ETH})
    period = _period()

    df_a = _outflow_df([(date(2025, 11, 1), Decimal("10")), (date(2025, 12, 1), Decimal("30"))])
    df_b = _outflow_df([(date(2025, 11, 1), Decimal("5")),  (date(2025, 12, 1), Decimal("20"))])
    src = _RecordingBalanceSource({
        (USDC.value, ALM_ETH.value, eoa1.holder_override.value): df_a,
        (USDC.value, ALM_ETH.value, eoa2.holder_override.value): df_b,
    })

    out = _build_paired_principal_caps(prime, anchor, period, src)

    merged = out[PAIRED_SOURCE.value]
    # Sum at each date: $15 then $50.
    assert merged.loc[merged["block_date"] == date(2025, 11, 1), "cum_inflow"].iloc[0] == Decimal("15")
    assert merged.loc[merged["block_date"] == date(2025, 12, 1), "cum_inflow"].iloc[0] == Decimal("50")
    assert len(src.calls) == 2


def test_two_eoas_with_distinct_paired_sources_keep_separate_caps():
    """Two display_only EOAs with DIFFERENT paired_source addresses should
    produce two distinct entries in the returned dict — no merge."""
    anchor = _anchor_venue()
    other_paired = _addr("0xcafe00000000000000000000000000000000cafe")
    eoa1 = _eoa_pair(vid="E_A",
                     holder_override=_addr("0xaaaa00000000000000000000000000000000aaaa"))
    eoa2 = _eoa_pair(vid="E_B",
                     paired_source=other_paired,
                     holder_override=_addr("0xbbbb00000000000000000000000000000000bbbb"))
    prime = _Prime(venues=[anchor, eoa1, eoa2], alm={Chain.ETHEREUM: ALM_ETH})
    period = _period()

    df_a = _outflow_df([(date(2025, 11, 1), Decimal("100"))])
    df_b = _outflow_df([(date(2025, 11, 1), Decimal("200"))])
    src = _RecordingBalanceSource({
        (USDC.value, ALM_ETH.value, eoa1.holder_override.value): df_a,
        (USDC.value, ALM_ETH.value, eoa2.holder_override.value): df_b,
    })

    out = _build_paired_principal_caps(prime, anchor, period, src)

    assert set(out.keys()) == {PAIRED_SOURCE.value, other_paired.value}
    assert out[PAIRED_SOURCE.value].iloc[0]["cum_inflow"] == Decimal("100")
    assert out[other_paired.value].iloc[0]["cum_inflow"] == Decimal("200")


def test_no_display_only_venues_returns_empty_dict():
    """A prime with no display_only venues at all → empty cap map, no source
    calls. Important: with an empty cap the classifier reverts to standard
    capital netting; this is the documented "no OOB pipeline" default."""
    anchor = _anchor_venue()
    prime = _Prime(venues=[anchor], alm={Chain.ETHEREUM: ALM_ETH})
    period = _period()
    src = _RecordingBalanceSource({})

    out = _build_paired_principal_caps(prime, anchor, period, src)
    assert out == {}
    assert src.calls == []
