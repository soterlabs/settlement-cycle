"""Historical gateway metadata may be absent, but must never conflict."""
from types import SimpleNamespace

import pytest

from settle.domain.config import load_prime_by_id
from settle.domain.primes import Chain
from settle.normalize.allocation_async_vaults import DEPOSIT_REQUEST, AsyncVaultCapital


def adapter(monkeypatch, asset):
    prime = load_prime_by_id("spark")
    venue = next(v for v in prime.venues if v.id == "S20")
    owner = prime.alm[Chain.ETHEREUM].hex
    key = (venue.token.address.hex, owner)
    vaults = AsyncVaultCapital(Chain.ETHEREUM, {key: venue})
    monkeypatch.setattr(vaults, "_read_address", lambda v, sig, b:
                        venue.token.address.hex if sig == "share()" else asset)
    row = SimpleNamespace(topic0=DEPOSIT_REQUEST, topic2="0x" + owner[2:].zfill(64),
                          address="0x36036ffd9b1c6966ab23209e073c68eb9a992f50",
                          block_number=22217641, log_index=1)
    return vaults, row, key, venue


def test_missing_underlying_resolved_from_authenticated_known_asset(monkeypatch):
    vaults, row, key, _ = adapter(monkeypatch, "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    assert vaults.prepare([row]) == [(row, key)]
    assert vaults.assets[key].symbol == "USDC"
    assert vaults.assets[key].decimals == 6


def test_unknown_asset_still_fails(monkeypatch):
    vaults, row, _, _ = adapter(monkeypatch, "0x" + "12" * 20)
    with pytest.raises(ValueError, match="asset mismatch"):
        vaults.prepare([row])


def test_explicit_underlying_conflict_still_fails(monkeypatch):
    from dataclasses import replace

    from settle.domain.sky_tokens import USDS_ETHEREUM

    vaults, row, key, venue = adapter(monkeypatch, "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    vaults.mapping[key] = replace(venue, underlying=USDS_ETHEREUM)
    with pytest.raises(ValueError, match="asset mismatch"):
        vaults.prepare([row])
