"""Unit tests for `settle.normalize.ssr` using a mock source."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from settle.domain import Chain, Month, Period
from settle.domain.config import load_prime
from settle.normalize.ssr import get_ssr_history
from settle.validation import SchemaError

from ..fixtures.mock_sources import MockSSRSource


def _obex(config_dir: Path):
    return load_prime(config_dir / "obex.yaml")


def _period() -> Period:
    return Period.from_month(Month(2026, 4), pin_blocks={Chain.ETHEREUM: 24971074})


def test_get_ssr_history_returns_source_output(config_dir: Path):
    src = MockSSRSource(pd.DataFrame({
        "effective_date": [date(2025, 12, 2), date(2025, 12, 16), date(2026, 3, 9)],
        "ssr_apy":        [0.0425,            0.0400,            0.0375],
    }))
    df = get_ssr_history(_obex(config_dir), _period(), source=src)
    assert len(df) == 3
    assert df.iloc[-1].ssr_apy == 0.0375


def test_get_ssr_history_uses_global_anchor_not_prime_start(config_dir: Path):
    """SSR is global to Sky — start is `SSR_HISTORY_ANCHOR`, NOT prime.start_date.
    Otherwise the first month of any prime would return an empty DataFrame if
    no rate changed during that month, and Compute would crash."""
    from settle.domain.sky_tokens import SSR_HISTORY_ANCHOR

    src = MockSSRSource()
    obex = _obex(config_dir)
    get_ssr_history(obex, _period(), source=src)
    assert src.calls == [(SSR_HISTORY_ANCHOR, 24971074)]
    assert SSR_HISTORY_ANCHOR < obex.start_date  # invariant — anchor is earlier


def test_get_ssr_history_rejects_prime_predating_anchor(config_dir: Path):
    """A prime that launched before SSR_HISTORY_ANCHOR can't be settled until
    the anchor is moved back — guard catches this at run-time."""
    from datetime import date as _date
    from settle.domain.primes import Prime

    obex = _obex(config_dir)
    pre_anchor = Prime(
        id=obex.id, ilk_bytes32=obex.ilk_bytes32,
        start_date=_date(2024, 1, 1),  # 8 months before SSR_HISTORY_ANCHOR
        subproxy=obex.subproxy, alm=obex.alm, venues=obex.venues,
    )
    with pytest.raises(ValueError, match="SSR_HISTORY_ANCHOR"):
        get_ssr_history(pre_anchor, _period(), source=MockSSRSource())


def test_get_ssr_history_rejects_bad_schema(config_dir: Path):
    src = MockSSRSource(pd.DataFrame({"date": [], "rate": []}))
    with pytest.raises(SchemaError):
        get_ssr_history(_obex(config_dir), _period(), source=src)


def test_get_ssr_history_requires_eth_pin(config_dir: Path):
    obex = _obex(config_dir)
    period_no_pin = Period.from_month(Month(2026, 4), pin_blocks={})
    with pytest.raises(ValueError, match="ethereum pin_block"):
        get_ssr_history(obex, period_no_pin, source=MockSSRSource())
