"""Integration checks on the new gating/raise behaviour in the V3 inflow path."""
from pathlib import Path

import pytest

from settle.domain.config import load_prime
from settle.extract.hypersync import HyperSyncError
from settle.normalize import positions as P
from settle.normalize.positions import _uniswap_v3_inflow_timeseries

grove = load_prime(Path("config/grove.yaml"))
E12 = next(v for v in grove.venues if v.id == "E12")


class _Src:
    def __init__(self, som, eom, raise_fees=False, fees=None):
        self._som, self._eom = som, eom
        self._raise, self._fees = raise_fees, fees or []
    def positions_in_pool(self, chain, owner, pool, block):
        return self._eom if block == 200 else self._som
    def liquidity_events_in_pool(self, chain, owner, pool, from_block, to_block):
        return []
    def fee_collections_in_pool(self, chain, owner, pool, from_block, to_block):
        if self._raise:
            raise HyperSyncError("Missing env var ENVIO_API_TOKEN")
        return self._fees


def test_dormant_venue_skips_the_fee_read_entirely():
    """Zero value at both boundaries -> nothing to harvest, no scan."""
    src = _Src([], [], raise_fees=True)   # would raise if called
    df = _uniswap_v3_inflow_timeseries(
        grove, E12, 100, 200, source=src, block_to_date=lambda b: "2026-08-01")
    assert df.empty


def test_fee_read_failure_raises_rather_than_degrading():
    """A live venue whose fee read fails must abort, not publish the
    pre-fix number."""
    class _Pos:
        token_id, amount0, amount1 = 1, 10**12, 10**12
        from settle.domain.primes import Address as _A
        token0 = _A.from_str("0x00000000efe302beaa2b3e6e1b18d08d69a9012a")
        token1 = _A.from_str("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    src = _Src([_Pos()], [_Pos()], raise_fees=True)
    with pytest.raises(P.UnsupportedPricingError, match="fee collections"):
        _uniswap_v3_inflow_timeseries(
            grove, E12, 100, 200, source=src, block_to_date=lambda b: "2026-08-01")
