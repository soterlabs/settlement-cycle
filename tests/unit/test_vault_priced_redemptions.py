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


def _patch_chain(monkeypatch, *, ts_by_block, eth_block_by_ts, rate_by_block):
    """Wire the venue-chain -> vault-chain translation deterministically.

    The seam that matters is ``block_timestamp`` + ``find_block_at_or_before``
    inside ``_vault_settlement_usd``: those are what carry the transfer's
    INSTANT across to the vault chain. Patching a block-resolver stub instead
    tests nothing, which is how the day-end bug shipped.
    """
    monkeypatch.setattr("settle.extract.hypersync.block_timestamp",
                        lambda chain, block: ts_by_block[block])
    monkeypatch.setattr("settle.extract.hypersync.find_block_at_or_before",
                        lambda chain, ts: eth_block_by_ts[ts])
    monkeypatch.setattr("settle.extract.rpc.convert_to_assets",
                        lambda chain, vault, shares, block:
                        shares * rate_by_block[block] // 10**18)


def test_each_transfer_is_priced_at_its_own_instant_not_the_day_end(monkeypatch):
    """Two redemptions on ONE day, with the vault stepping between them.

    This is Grove E22 on 2026-05-12: the exit executed at 19:45 and the vault
    moved before midnight, so pricing both at the day-end block missed the
    cash by $30,028 — no improvement over the oracle at all. Each transfer
    must be priced at the block matching its own timestamp.
    """
    _patch_chain(
        monkeypatch,
        ts_by_block={700: 1000, 800: 2000},          # two plume blocks, one day
        eth_block_by_ts={1000: 10, 2000: 20},        # -> two DIFFERENT eth blocks
        rate_by_block={10: 1_000_000, 20: 2_000_000},  # 1.00 then 2.00 (6dp)
    )
    monkeypatch.setattr(
        "settle.extract.hypersync.query_logs",
        lambda *a, **k: QueryResult(rows=[_row(10**18, 700), _row(10**18, 800, 2)]),
    )
    out = P._vault_priced_redemptions_by_date(
        _Prime(), _Venue(), _Period(), block_resolver=_Resolver(),
    )
    (usd, tokens), = out.values()
    assert tokens == Decimal("2"), "both share amounts accumulate"
    # 1 share @ $1.00 + 1 share @ $2.00. Day-end pricing would give 2.00 or
    # 4.00 — never 3.00 — so this assertion is what pins per-transfer pricing.
    assert usd == Decimal("3"), usd


def test_gross_tokens_are_returned_so_a_deposit_on_the_same_day_survives(monkeypatch):
    """A day carrying BOTH a deposit and a redemption must keep the deposit.

    The caller needs the outbound share count to re-price only the inflow leg
    at the oracle. Substituting the gross outflow wholesale would drop it.
    """
    _patch_chain(monkeypatch, ts_by_block={700: 1000},
                 eth_block_by_ts={1000: 10}, rate_by_block={10: 1_020_232})
    monkeypatch.setattr(
        "settle.extract.hypersync.query_logs",
        lambda *a, **k: QueryResult(rows=[_row(3 * 10**18, 700)]),
    )
    out = P._vault_priced_redemptions_by_date(
        _Prime(), _Venue(), _Period(), block_resolver=_Resolver(),
    )
    (usd, tokens), = out.values()
    assert tokens == Decimal("3"), "share count must come back, not just USD"
    assert usd == Decimal("3.060696")


def test_zero_from_the_vault_raises_rather_than_valuing_at_nothing(monkeypatch):
    """A multi-million redemption silently valued at $0 would be booked as a
    total loss."""
    monkeypatch.setattr("settle.extract.hypersync.block_timestamp",
                        lambda chain, block: 1)
    monkeypatch.setattr("settle.extract.hypersync.find_block_at_or_before",
                        lambda chain, ts: 10)
    monkeypatch.setattr("settle.extract.rpc.convert_to_assets",
                        lambda chain, vault, shares, block: 0)
    with pytest.raises(P.UnsupportedPricingError, match="convertToAssets=0"):
        P._vault_settlement_usd(_Venue(), Decimal("12020502.0395381"), 100)


def test_reads_the_vault_on_its_own_chain_at_the_translated_block(monkeypatch):
    """ACRDX's token is on Plume; its vault is on Ethereum. Reading the vault
    at a Plume block number would hit an unrelated (or future) block."""
    calls = []
    monkeypatch.setattr("settle.extract.hypersync.block_timestamp",
                        lambda chain, block: 4242)
    monkeypatch.setattr("settle.extract.hypersync.find_block_at_or_before",
                        lambda chain, ts: 25_081_215 if ts == 4242 else -1)
    monkeypatch.setattr(
        "settle.extract.rpc.convert_to_assets",
        lambda chain, vault, shares, block: calls.append((chain, block)) or 10**6,
    )
    P._vault_settlement_usd(_Venue(), Decimal("1"), 86_400_000)
    assert calls == [(Chain.ETHEREUM, 25_081_215)], calls


def test_share_and_asset_decimals_are_taken_from_config(monkeypatch):
    """18dp shares in, 6dp assets out."""
    captured = {}

    def fake(chain, vault, shares, block):
        captured["shares"] = shares
        return 1_020_232          # 1.020232 in 6dp
    monkeypatch.setattr("settle.extract.hypersync.block_timestamp",
                        lambda chain, block: 1)
    monkeypatch.setattr("settle.extract.hypersync.find_block_at_or_before",
                        lambda chain, ts: 10)
    monkeypatch.setattr("settle.extract.rpc.convert_to_assets", fake)
    got = P._vault_settlement_usd(_Venue(), Decimal("1"), 100)
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


def test_asset_decimals_must_be_configured_not_defaulted(tmp_path):
    """Defaulting them would misprice an 18-decimal vault by 1e12."""
    cfg = _venue_cfg(nav_oracle={
        "kind": "chronicle", "address": "0x" + "aa" * 20,
        "fallback": "erc4626", "fallback_address": "0x" + "bb" * 20,
    })
    with pytest.raises(ValueError, match="fallback_underlying_decimals"):
        _load(cfg, tmp_path)


# ── fail-closed behaviour ────────────────────────────────────────────────

def _bal_df(rows):
    import pandas as pd
    return pd.DataFrame({"block_date": [r[0] for r in rows],
                         "daily_net": [r[1] for r in rows]})


class _BalSrc:
    def __init__(self, rows):
        self._rows = rows

    def cumulative_balance_timeseries(self, **kw):
        return _bal_df(self._rows)


def _run_ts(monkeypatch, rows, *, log_raises):
    """Drives the real E22 venue (flag on, Plume token, Ethereum vault) rather
    than a stub, so the assertion holds against the actual config."""
    from pathlib import Path

    from settle.domain.config import load_prime
    from settle.extract.hypersync import HyperSyncError

    grove = load_prime(Path("config/grove.yaml"))
    e22 = next(v for v in grove.venues if v.id == "E22")
    assert e22.redemptions_priced_at_vault, "fixture assumes the flag is on"
    if log_raises:
        def boom(*a, **k):
            raise HyperSyncError("no token")
        monkeypatch.setattr("settle.extract.hypersync.query_logs", boom)
    else:
        monkeypatch.setattr("settle.extract.hypersync.query_logs",
                            lambda *a, **k: QueryResult(rows=[]))
    return P._rwa_inflow_timeseries(
        grove, e22, _Period(),
        balance_source=_BalSrc(rows),
        block_resolver=_Resolver(),
        nav_at_block=lambda b: Decimal("1.02"),
    )


def test_unreadable_log_raises_when_the_period_has_a_redemption(monkeypatch):
    """Degrading to oracle pricing reproduces exactly the pre-fix number
    ($30,028.18 too high on E22's May exit), which reads as an ordinary month
    rather than an error."""
    with pytest.raises(P.UnsupportedPricingError, match="transfer log"):
        _run_ts(monkeypatch, [(date(2026, 8, 10), "-100")], log_raises=True)


def test_unreadable_log_is_harmless_when_there_is_no_redemption(monkeypatch):
    """A venue with no exits must not be blocked by an unreadable log."""
    out = _run_ts(monkeypatch, [(date(2026, 8, 10), "100")], log_raises=True)
    assert not out.empty
    assert out["daily_inflow"].iloc[0] == Decimal("100") * Decimal("1.02")


def test_a_date_key_mismatch_raises_instead_of_reverting_silently(monkeypatch):
    """Map keys come from the transfer's block; daily rows from the balance
    series. If those two date sources disagree, the lookup misses and the day
    would fall back to NAV-oracle pricing with no signal — which is the bug
    this whole path exists to remove.
    """
    from pathlib import Path

    from settle.domain.config import load_prime

    grove = load_prime(Path("config/grove.yaml"))
    e22 = next(v for v in grove.venues if v.id == "E22")
    monkeypatch.setattr("settle.extract.hypersync.block_timestamp",
                        lambda chain, block: 1)
    monkeypatch.setattr("settle.extract.hypersync.find_block_at_or_before",
                        lambda chain, ts: 10)
    monkeypatch.setattr("settle.extract.rpc.convert_to_assets",
                        lambda chain, vault, shares, block: 10**6)
    monkeypatch.setattr(
        "settle.extract.hypersync.query_logs",
        lambda *a, **k: QueryResult(rows=[_row(10**18, 700)]),
    )

    class _Off(_Resolver):
        def block_to_date(self, chain, block):
            return date(2026, 8, 25)          # balance row says the 10th

    with pytest.raises(P.UnsupportedPricingError, match="found no matching row"):
        P._rwa_inflow_timeseries(
            grove, e22, _Period(),
            balance_source=_BalSrc([(date(2026, 8, 10), "-1")]),
            block_resolver=_Off(),
            nav_at_block=lambda b: Decimal("1.02"),
        )


def test_a_pure_redemption_day_never_touches_the_nav_oracle(monkeypatch):
    """This venue class is where the oracle is least reliable — ACRDX's
    Chronicle feed froze for Jun+Jul 2026 on a rotated-out consumer. A day
    with no deposit leg needs no NAV at all."""
    from pathlib import Path

    from settle.domain.config import load_prime

    grove = load_prime(Path("config/grove.yaml"))
    e22 = next(v for v in grove.venues if v.id == "E22")
    monkeypatch.setattr("settle.extract.hypersync.block_timestamp",
                        lambda chain, block: 1)
    monkeypatch.setattr("settle.extract.hypersync.find_block_at_or_before",
                        lambda chain, ts: 10)
    monkeypatch.setattr("settle.extract.rpc.convert_to_assets",
                        lambda chain, vault, shares, block: 5_000_000)
    monkeypatch.setattr(
        "settle.extract.hypersync.query_logs",
        lambda *a, **k: QueryResult(rows=[_row(10**18, 700)]),
    )

    def _boom(_b):
        raise AssertionError("the NAV oracle must not be read")

    out = P._rwa_inflow_timeseries(
        grove, e22, _Period(),
        balance_source=_BalSrc([(date(2026, 8, 10), "-1")]),
        block_resolver=_Resolver(),
        nav_at_block=_boom,
    )
    assert out["daily_inflow"].iloc[0] == Decimal("-5")
