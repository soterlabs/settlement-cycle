"""Unit tests for `settle.normalize.positions`."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from settle.domain import Chain, Month, Period, PricingCategory
from settle.domain.config import load_prime
from settle.normalize.positions import get_position_balance, get_position_value

from replay.mock_sources import MockConvertToAssetsSource, MockPositionBalanceSource


def _obex(config_dir: Path):
    return load_prime(config_dir / "obex.yaml")


def _eth_pin(block: int = 24971074) -> Period:
    return Period.from_month(Month(2026, 4), pin_blocks={Chain.ETHEREUM: block})


# --- get_position_balance ---------------------------------------------------

def test_position_balance_calls_source_with_alm_holder(config_dir: Path):
    obex = _obex(config_dir)
    venue = obex.venues[0]                                  # syrupUSDC, decimals=6
    src = MockPositionBalanceSource(raw_balance=525_698_254_197_051)

    bal = get_position_balance(obex, venue, block=24971074, source=src)

    assert bal == Decimal("525698254.197051")               # raw / 10^6
    chain, token, holder, block = src.calls[0]
    assert chain == "ethereum"
    assert token == venue.token.address.value
    assert holder == obex.alm[Chain.ETHEREUM].value
    assert block == 24971074


def test_position_balance_zero_when_source_returns_zero(config_dir: Path):
    obex = _obex(config_dir)
    venue = obex.venues[0]
    bal = get_position_balance(obex, venue, block=0, source=MockPositionBalanceSource(raw_balance=0))
    assert bal == Decimal("0")


def test_position_balance_rejects_chain_without_alm(config_dir: Path):
    obex = _obex(config_dir)
    venue = obex.venues[0]
    obex_no_alm = type(obex)(
        id=obex.id, ilk_bytes32=obex.ilk_bytes32, start_date=obex.start_date,
        subproxy=obex.subproxy, alm={}, venues=obex.venues,
    )
    with pytest.raises(ValueError, match="no ALM"):
        get_position_balance(obex_no_alm, venue, block=0, source=MockPositionBalanceSource())


# --- get_position_value -----------------------------------------------------

def test_position_value_for_obex_syrup_usdc(config_dir: Path):
    """OBEX V1: 525_698_254 syrupUSDC × ($1.07 USD/share) ≈ $562.5M.

    Numbers chosen to mirror live MCP/RPC observations from the POC. Math:
    raw_balance = 525_698_254_197_051   (6 share-decimals)
    convertToAssets(1e6) = 1_070_000    (USDC = 6 decimals; pps = 1.07)
    """
    obex = _obex(config_dir)
    venue = obex.venues[0]                                  # cat B
    bal_src = MockPositionBalanceSource(raw_balance=525_698_254_197_051)
    price_src = MockConvertToAssetsSource(raw_assets=1_070_000)

    value = get_position_value(
        obex, venue, block=24971074,
        balance_source=bal_src, erc4626_source=price_src,
    )
    expected = Decimal("525698254.197051") * Decimal("1.07")
    assert value == expected
    # Sanity bound: somewhere between $560M and $565M.
    assert Decimal("560_000_000") < value < Decimal("565_000_000")


def test_position_value_par_stable_uses_const_one(config_dir: Path):
    """For category A, no convertToAssets call should happen — price is $1 const."""
    obex = _obex(config_dir)
    base_venue = obex.venues[0]
    par_venue = type(base_venue)(
        id="V_par",
        chain=Chain.ETHEREUM,
        token=base_venue.underlying,                        # USDC
        pricing_category=PricingCategory.PAR_STABLE,
        underlying=None,
    )
    bal_src = MockPositionBalanceSource(raw_balance=10_000_000)  # 10 USDC raw
    price_src = MockConvertToAssetsSource(raw_assets=999_999_999)  # should NOT be called

    value = get_position_value(
        obex, par_venue, block=0,
        balance_source=bal_src, erc4626_source=price_src,
    )
    assert value == Decimal("10")
    assert price_src.calls == [], "convertToAssets must not be called for par stables"


# --- Category EOA: flow-accounted balance -----------------------------------

class _RoutingBalanceSource:
    """Mock IBalanceSource that returns different frames per directed query.

    Routes ``directed_inflow_timeseries(from→to)`` to a dict keyed by
    ``(token_hex, from_hex, to_hex)``. Anything not in the dict returns empty.
    Used to exercise the EOA balance path which queries two distinct legs.
    """

    def __init__(self, by_route):
        self.by_route = by_route
        self.calls = []

    def directed_inflow_timeseries(
        self, chain, token, from_addr, to_addr, start, pin_block,
    ):
        import pandas as pd
        key = (token.hex(), from_addr.hex(), to_addr.hex())
        self.calls.append((chain, key, start, pin_block))
        total = self.by_route.get(key)
        if total is None:
            return pd.DataFrame({"block_date": [], "daily_inflow": [], "cum_inflow": []})
        from datetime import date as _date
        return pd.DataFrame({
            "block_date": [_date(2026, 1, 1)],
            "daily_inflow": [total],
            "cum_inflow": [total],
        })

    # Unused Protocol methods — return empty frames.
    def cumulative_balance_timeseries(self, *a, **kw):
        import pandas as pd
        return pd.DataFrame({"block_date": [], "daily_net": [], "cum_balance": []})

    def inflow_by_counterparty(self, *a, **kw):
        import pandas as pd
        return pd.DataFrame({"block_date": [], "counterparty": [], "signed_amount": []})


def _grove(config_dir: Path):
    from settle.domain.config import load_prime
    return load_prime(config_dir / "grove.yaml")


def _e36_routing(grove, principal=None, drain=None):
    """Build a routing source keyed off E36's actual addresses."""
    e36 = next(v for v in grove.venues if v.id == "E36")
    e14 = next(v for v in grove.venues if v.id == "E14")
    alm = grove.alm[Chain.ETHEREUM]
    routes = {}
    if principal is not None:
        routes[(e36.token.address.value.hex(),
                alm.value.hex(),
                e36.holder_override.value.hex())] = principal
    if drain is not None:
        routes[(e14.token.address.value.hex(),
                e36.paired_source.value.hex(),
                alm.value.hex())] = drain
    return e36, _RoutingBalanceSource(routes)


def test_eoa_balance_principal_only_no_drain(config_dir: Path):
    """Before any return arrives, balance = full principal sent to the EOA."""
    grove = _grove(config_dir)
    e36, src = _e36_routing(grove, principal=Decimal("50_000_000"))
    bal = get_position_balance(grove, e36, block=24_034_937, flow_source=src)
    assert bal == Decimal("50_000_000")


def test_eoa_balance_partial_drain(config_dir: Path):
    """After partial return, balance = principal − cumulative anchor inflow."""
    grove = _grove(config_dir)
    e36, src = _e36_routing(
        grove,
        principal=Decimal("50_000_000"),
        drain=Decimal("25_000_000"),
    )
    bal = get_position_balance(grove, e36, block=24_500_000, flow_source=src)
    assert bal == Decimal("25_000_000")


def test_eoa_balance_full_drain_with_spread(config_dir: Path):
    """Full return slightly over par → balance is slightly negative (= spread)."""
    grove = _grove(config_dir)
    e36, src = _e36_routing(
        grove,
        principal=Decimal("50_000_000"),
        drain=Decimal("50_120_757"),
    )
    bal = get_position_balance(grove, e36, block=24_888_426, flow_source=src)
    # Negative = realized venue spread (yield captured in the OOB roundtrip).
    assert bal == Decimal("-120_757")


def test_eoa_balance_rejects_missing_pairing(config_dir: Path):
    """Cat EOA venues without paired_with or paired_source must fail loudly."""
    from dataclasses import replace

    grove = _grove(config_dir)
    e36, src = _e36_routing(grove)
    broken = replace(e36, paired_with=None, paired_source=None)

    with pytest.raises(ValueError, match="paired_with and paired_source"):
        get_position_balance(grove, broken, block=0, flow_source=src)


def test_eoa_pricing_returns_par(config_dir: Path):
    """Cat EOA unit price = $1 (par-stable USDC)."""
    from settle.normalize.prices import get_unit_price

    grove = _grove(config_dir)
    e36 = next(v for v in grove.venues if v.id == "E36")
    assert get_unit_price(e36, block=0) == Decimal("1.00")


def test_eoa_rejects_non_par_stable_anchor(config_dir: Path):
    """Anchor must be Cat A par-stable — pairing with Cat B/C/E etc. is wrong.

    Pairing with a Cat B share-token (e.g. bbqAUSD shares) would have the
    drain leg query share Transfers, which never fire from paired_source.
    Drain would silently come back as zero and the venue would never drain.
    Detect this at first balance read.
    """
    from dataclasses import replace
    grove = _grove(config_dir)
    e36, src = _e36_routing(grove)
    # Re-point E36 to E6 (Cat B bbqAUSD) — a Morpho 4626 share, not par-stable.
    broken = replace(e36, paired_with="E6")
    with pytest.raises(ValueError, match="not PAR_STABLE"):
        get_position_balance(grove, broken, block=0, flow_source=src)


def test_eoa_rejects_unknown_anchor(config_dir: Path):
    """paired_with pointing to a nonexistent venue id must fail loudly."""
    from dataclasses import replace
    grove = _grove(config_dir)
    e36, src = _e36_routing(grove)
    broken = replace(e36, paired_with="E_does_not_exist")
    with pytest.raises(ValueError, match="does not match any venue id"):
        get_position_balance(grove, broken, block=0, flow_source=src)
