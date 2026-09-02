"""Cat E redemptions must be valued at what they settle for, not at the oracle.

The NAV oracle and the vault's own ``convertToAssets`` are both live and
correct but disagree intra-period: the oracle accrues near-daily while the
vault steps irregularly. Redemptions settle at the VAULT price, so pricing an
outflow off the oracle overstates it — and therefore overstates revenue, which
is the residual.

Grove E22 (ACRDX), verified against cash on both 2026 exits:
    2026-05-11  cash 18,077,674.30  vs convertToAssets 18,077,674.283942
    2026-08-10  cash 12,263,706.47  vs convertToAssets 12,263,706.449767
Booked at the oracle instead, those overstated revenue by $30,028.18 and
$19,522.75.

Pricing is per transfer at that transfer's OWN block, not at day end: on
2026-05-12 the vault stepped 0.088% between the 19:45 redemption and midnight,
so an end-of-day read misses the cash by $15,896 on $18.08M.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from settle.domain.primes import Address, Chain, PricingCategory
from settle.extract.hypersync import LogRow, QueryResult
from settle.normalize import positions as P

VAULT = Address.from_str("0x74a739ea1dc67c5a0179ebad665d1d3c4b80b712")
HOLDER = Address.from_str("0x1db91ad50446a671e2231f77e00948e68876f812")
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class _Oracle:
    kind = "chronicle"
    address = Address.from_str("0x" + "aa" * 20)
    fallback = "erc4626"
    fallback_address = VAULT
    fallback_underlying_decimals = 6
    oracle_chain = Chain.ETHEREUM


class _Tok:
    address = Address.from_str("0x9477724bb54ad5417de8baff29e59df3fb4da74f")
    symbol = "ACRDX"
    decimals = 18


class _Venue:
    id = "E22"
    chain = Chain.PLUME
    token = _Tok()
    holder_override = None
    nav_oracle = _Oracle()
    redemptions_priced_at_vault = True
    pricing_category = PricingCategory.RWA_TRANCHE


class _Prime:
    start_date = date(2026, 1, 1)

    @property
    def alm(self):
        return {Chain.PLUME: HOLDER}


class _Resolver:
    """Plume block <-> date, and the Ethereum block for the same day-end."""

    def block_at_or_before(self, chain, when):
        return {"plume": 86_400_000, "ethereum": 25_700_000}[chain]

    def block_to_date(self, chain, block):
        return date(2026, 8, 10)


class _Period:
    start = date(2026, 8, 1)
    end = date(2026, 8, 31)

    @property
    def pin_blocks(self):
        return {Chain.PLUME: 86_500_000}


def _row(raw: int, block: int, idx: int = 1):
    return LogRow(block_number=block, log_index=idx, block_time=0,
                  address=_Tok.address.hex, topic0=TRANSFER,
                  topic1="0x" + HOLDER.hex[2:].rjust(64, "0"),
                  topic2=None, topic3=None, data=hex(raw), transaction_hash="0xab")


def test_prices_each_transfer_at_its_own_block(monkeypatch):
    """Two transfers on one day, priced at DIFFERENT blocks — the vault moved
    between them, which is exactly the May 2026 case."""
    seen = []

    def fake_cta(chain, vault, shares, block):
        seen.append((shares, block))
        rate = 1_020_232 if block == 100 else 1_030_000   # 6dp per 1e18 shares
        return shares * rate // 10**18

    monkeypatch.setattr("settle.extract.rpc.convert_to_assets", fake_cta)
    monkeypatch.setattr(
        "settle.extract.hypersync.query_logs",
        lambda *a, **k: QueryResult(rows=[_row(10**18, 100), _row(2 * 10**18, 200, 2)]),
    )

    class _R(_Resolver):
        def block_at_or_before(self, chain, when):
            # identity: the helper translates plume->ethereum per calendar day
            return {"plume": 86_400_000, "ethereum": 100}[chain]

    out = P._vault_priced_redemptions_by_date(
        _Prime(), _Venue(), _Period(), block_resolver=_R(),
    )
    # both transfers land on the same date but were priced separately
    assert len(out) == 1
    assert len(seen) == 2, "each transfer must be priced individually"


def test_zero_from_the_vault_raises_rather_than_valuing_at_nothing(monkeypatch):
    """A multi-million redemption silently valued at $0 would be booked as a
    total loss."""
    monkeypatch.setattr("settle.extract.rpc.convert_to_assets",
                        lambda chain, vault, shares, block: 0)
    with pytest.raises(P.UnsupportedPricingError, match="convertToAssets=0"):
        P._vault_settlement_usd(
            _Venue(), Decimal("12020502.0395381"), 100, block_resolver=_Resolver(),
        )


def test_reads_the_vault_on_its_own_chain_not_the_venue_chain(monkeypatch):
    """ACRDX's token is on Plume; its vault is on Ethereum. Reading the vault
    at a Plume block number would hit an unrelated (or future) block."""
    calls = []
    monkeypatch.setattr(
        "settle.extract.rpc.convert_to_assets",
        lambda chain, vault, shares, block: calls.append((chain, block)) or 10**6,
    )
    P._vault_settlement_usd(
        _Venue(), Decimal("1"), 86_400_000, block_resolver=_Resolver(),
    )
    assert calls == [(Chain.ETHEREUM, 25_700_000)], calls


def test_share_and_asset_decimals_are_taken_from_config(monkeypatch):
    """18dp shares in, 6dp assets out."""
    captured = {}

    def fake(chain, vault, shares, block):
        captured["shares"] = shares
        return 1_020_232          # 1.020232 in 6dp
    monkeypatch.setattr("settle.extract.rpc.convert_to_assets", fake)
    got = P._vault_settlement_usd(
        _Venue(), Decimal("1"), 100, block_resolver=_Resolver(),
    )
    assert captured["shares"] == 10**18, "1 share at 18dp"
    assert got == Decimal("1.020232"), "raw/1e6"


# ── config validation: a typo must not silently fall back to the bug ──────

def _venue_cfg(**over):
    cfg = {
        "id": "X1", "chain": "plume", "label": "t",
        "pricing_category": "E",
        "token": {"address": "0x" + "11" * 20, "symbol": "T", "decimals": 18},
        "nav_oracle": {"kind": "chronicle", "address": "0x" + "aa" * 20,
                       "fallback": "erc4626",
                       "fallback_address": "0x" + "bb" * 20,
                       "fallback_underlying_decimals": 6},
        "redemptions_priced_at_vault": True,
    }
    cfg.update(over)
    return cfg


def _load(venue_cfg, tmp_path):
    import yaml

    from settle.domain.config import load_prime
    doc = {"id": "t", "ilk_bytes32": "0x" + "00" * 32,
           "start_date": "2026-01-01",
           "addresses": {"plume": {"alm": "0x" + "22" * 20}},
           "venues": [venue_cfg]}
    f = tmp_path / "t.yaml"
    f.write_text(yaml.safe_dump(doc))
    return load_prime(f)


def test_flag_is_rejected_on_a_non_cat_e_venue(tmp_path):
    cfg = _venue_cfg(pricing_category="A")
    cfg.pop("nav_oracle")
    with pytest.raises(ValueError, match="RWA_TRANCHE"):
        _load(cfg, tmp_path)


def test_flag_is_rejected_without_a_vault_address(tmp_path):
    cfg = _venue_cfg(nav_oracle={"kind": "chronicle", "address": "0x" + "aa" * 20})
    with pytest.raises(ValueError, match="fallback_address"):
        _load(cfg, tmp_path)


def test_flag_defaults_off(tmp_path):
    cfg = _venue_cfg()
    del cfg["redemptions_priced_at_vault"]
    prime = _load(cfg, tmp_path)
    assert prime.venues[0].redemptions_priced_at_vault is False
