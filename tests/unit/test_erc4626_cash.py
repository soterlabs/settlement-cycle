"""Cash capital, ownership semantics, gross-flow checks and live route regressions."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal as D
from types import SimpleNamespace

import pandas as pd
import pytest

from settle.compute.prime_agent_revenue import VenueRevenueInputs, compute_venue_revenue
from settle.domain import Chain, Month, Period
from settle.domain.config import load_prime_by_id
from settle.extract.hypersync import LogRow
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.erc4626_cash import cash_inflow_timeseries
from settle.normalize.sources.hypersync_venue_events import DEPOSIT, WITHDRAW


def fixture():
    prime = load_prime_by_id("grove")
    venue = next(v for v in prime.venues if v.id == "E19")
    period = Period(date(2027, 4, 1), date(2027, 4, 2), {Chain.BASE: 200})
    holder = "0x" + prime.alm[Chain.BASE].hex[2:].zfill(64)
    vault = "0x" + venue.token.address.hex[2:].zfill(64)
    caller = "0x" + "ab" * 32
    zero = "0x" + "0" * 64

    def row(index, address, topic, first, second, third=None, words=(), tx=None):
        return LogRow(
            150,
            index,
            int(datetime(2027, 4, 1, tzinfo=UTC).timestamp()),
            address,
            topic,
            first,
            second,
            third,
            "0x" + "".join(format(v, "064x") for v in words),
            tx or "0x" + "12" * 32,
        )

    def deposit(assets=100, shares=100):
        return [
            row(0, venue.token.address.hex, TRANSFER_TOPIC0, zero, holder, words=[shares * 10**18]),
            row(
                1,
                venue.underlying.address.hex,
                TRANSFER_TOPIC0,
                holder,
                vault,
                words=[assets * 10**6],
            ),
            row(
                2,
                venue.token.address.hex,
                DEPOSIT,
                caller,
                holder,
                words=[assets * 10**6, shares * 10**18],
            ),
        ]

    def withdraw(assets=110, shares=100):
        return [
            row(3, venue.token.address.hex, TRANSFER_TOPIC0, holder, zero, words=[shares * 10**18]),
            row(
                4,
                venue.underlying.address.hex,
                TRANSFER_TOPIC0,
                vault,
                holder,
                words=[assets * 10**6],
            ),
            row(
                5,
                venue.token.address.hex,
                WITHDRAW,
                caller,
                holder,
                holder,
                [assets * 10**6, shares * 10**18],
            ),
        ]

    def run(rows, opening=0, closing=100 * 10**18, venue=venue, fetch=None):
        return cash_inflow_timeseries(
            prime,
            venue,
            period,
            opening_block=100,
            balance_at=lambda c, t, h, b: opening if b == 100 else closing,
            fetch=fetch or (lambda *a, **k: rows),
        )

    return SimpleNamespace(
        prime=prime,
        venue=venue,
        period=period,
        deposit=deposit,
        withdraw=withdraw,
        run=run,
        holder=holder,
        vault=vault,
    )


def test_cash_deposit_credits_post_deposit_yield_and_uses_owner_not_caller():
    f = fixture()
    frame = f.run(f.deposit())
    result = compute_venue_revenue(
        f.period,
        VenueRevenueInputs(
            venue=f.venue, value_som=D(0), value_eom=D(110), inflow_timeseries=frame
        ),
    )
    assert result.period_inflow == 100
    assert result.revenue == 10
    assert result.tw_avg_value == 100


def test_full_exit_and_same_day_round_trip_keep_actual_cash_yield():
    f = fixture()
    for rows, opening, value_som in [
        (f.withdraw(), 100 * 10**18, 100),
        (f.deposit() + f.withdraw(), 0, 0),
    ]:
        frame = f.run(rows, opening=opening, closing=0)
        result = compute_venue_revenue(
            f.period,
            VenueRevenueInputs(
                venue=f.venue, value_som=D(value_som), value_eom=D(0), inflow_timeseries=frame
            ),
        )
        assert result.revenue == 10


def test_empty_period_still_requires_unchanged_share_balances():
    f = fixture()
    assert f.run([], opening=100, closing=100).empty
    with pytest.raises(ValueError, match="pinned wallet balances"):
        f.run([], opening=0, closing=100)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda rows: rows[:2], "gross share"),
        (
            lambda rows: [
                rows[0],
                replace(rows[1], data="0x" + format(99 * 10**6, "064x")),
                rows[2],
            ],
            "actual holder/vault cash",
        ),
        (
            lambda rows: [replace(rows[0], topic1="0x" + "34" * 32), *rows[1:]],
            "Direct ERC4626 share transfer",
        ),
        (lambda rows: [replace(rows[0], transaction_hash=None), *rows[1:]], "unidentified"),
        (lambda rows: [replace(rows[0], block_number=99), *rows[1:]], "Unpinned"),
    ],
)
def test_incomplete_or_unexplained_flows_stop_calculation(mutation, match):
    f = fixture()
    with pytest.raises(ValueError, match=match):
        f.run(mutation(f.deposit()))


def test_repeated_source_logs_deduplicate_but_conflicts_fail():
    f = fixture()
    rows = f.deposit()
    assert f.run(rows + rows).iloc[-1].cum_inflow == 100
    with pytest.raises(ValueError, match="Conflicting"):
        f.run([*rows, replace(rows[0], data="0x" + format(1, "064x"))])


def test_transport_failure_is_not_an_empty_capital_frame():
    f = fixture()

    def failed(*a, **kw):
        raise RuntimeError("Archive unavailable")

    with pytest.raises(RuntimeError, match="Archive unavailable"):
        f.run([], fetch=failed)


def test_zero_asset_zero_share_withdrawal_can_have_another_receiver():
    f = fixture()
    rows = f.withdraw(0, 0)
    rows[-1] = replace(rows[-1], topic2=f.vault)
    assert f.run(rows, opening=0, closing=0).iloc[-1].cum_inflow == 0


def test_different_receiver_with_nonzero_assets_requires_review():
    f = fixture()
    rows = f.withdraw()
    rows[-1] = replace(rows[-1], topic2=f.vault)
    with pytest.raises(ValueError, match="owner/receiver"):
        f.run(rows, opening=100 * 10**18, closing=0)


def test_opt_in_is_strict_and_limited_to_three_base_vaults():
    f = fixture()
    assert [v.id for v in f.prime.venues if v.erc4626_cash_flows] == ["E19", "E23", "E43"]
    for changes in [
        dict(erc4626_cash_flows="false"),
        dict(event_source="dune"),
        dict(sky_savings_token=True),
        dict(share_burn_destinations=[f.prime.alm[Chain.BASE]]),
    ]:
        with pytest.raises(ValueError):
            replace(f.venue, **changes)
    for prime in ["spark", "obex", "osero"]:
        assert not any(v.erc4626_cash_flows for v in load_prime_by_id(prime).venues)


def test_monthly_orchestration_routes_cash_without_changing_other_vaults(monkeypatch):
    from settle.compute import Sources, compute_monthly_pnl
    from tests.fixtures.mock_sources import (
        MockBalanceSource,
        MockBlockResolver,
        MockConvertToAssetsSource,
        MockDebtSource,
        MockPositionBalanceSource,
        MockSSRSource,
    )

    f = fixture()
    # Future synthetic period, all sources injected; no historical monthly run.
    venue = replace(f.venue, chain=Chain.ETHEREUM)
    prime = replace(load_prime_by_id("obex"), venues=[venue])
    monkeypatch.setattr("settle.extract.rpc.ilk_rate", lambda *a, **k: 10**27)

    def cash(*a, **kw):
        return pd.DataFrame(
            {"block_date": [date(2027, 4, 1)], "daily_inflow": [D(100)], "cum_inflow": [D(100)]}
        )

    seen = []

    def tracked(*a, **kw):
        seen.append(kw)
        return cash()

    monkeypatch.setattr("settle.normalize.erc4626_cash.cash_inflow_timeseries", tracked)

    class Prices(MockConvertToAssetsSource):
        def convert_to_assets(self, chain, vault, shares, block):
            return 1_000_000 if block == 100 else 1_100_000

    sources = Sources(
        block_resolver=MockBlockResolver(default=200),
        debt=MockDebtSource(
            pd.DataFrame({"block_date": [date(2027, 4, 1)], "daily_dart": [0], "cum_debt": [0]})
        ),
        balance=MockBalanceSource(),
        ssr=MockSSRSource(pd.DataFrame({"effective_date": [date(2027, 4, 1)], "ssr_apy": [0]})),
        position_balance=MockPositionBalanceSource(raw_balance=0),
        convert_to_assets=Prices(),
        erc4626_cash_logs=lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        sources.position_balance,
        "balance_at",
        lambda chain, token, holder, block: (
            100 * 10**18 if token == venue.token.address.value and block == 200 else 0
        ),
    )
    result = compute_monthly_pnl(
        prime,
        Month(2027, 4),
        sources=sources,
        pin_blocks_som={Chain.ETHEREUM: 100},
        pin_blocks_eom={Chain.ETHEREUM: 200},
    )
    assert result.prime_agent_revenue == 10
    assert len(seen) == 1 and seen[0]["fetch"] is sources.erc4626_cash_logs
    seen.clear()
    compute_monthly_pnl(
        replace(prime, venues=[replace(venue, erc4626_cash_flows=False)]),
        Month(2027, 4),
        sources=sources,
        pin_blocks_som={Chain.ETHEREUM: 100},
        pin_blocks_eom={Chain.ETHEREUM: 200},
    )
    assert not seen


def test_historical_component_replay_and_floor_preserve_prior_periods():
    from scripts.reconcile_grove_base_cash import replay

    result = replay()
    assert len(result["results"]) == 30
    assert D(result["income_delta"]["jan_sep"]).quantize(D(".01")) == D("13769.34")
    assert D(result["income_delta"]["october"]).quantize(D(".01")) == D("8064.96")
    assert all(D(r["global_cof_delta"]) == 0 for r in result["results"])
    for row in result["e12_weights"]:
        if row["month"] < "2026-09":
            assert row["old_cof_weight"] == row["new_cof_weight"]
    september = next(r for r in result["e12_weights"] if r["month"] == "2026-09")
    october = next(r for r in result["e12_weights"] if r["month"] == "2026-10")
    assert D(september["new_notional"]) == 10000000
    assert D(september["new_cof_weight"]) == D("17786194.413708")
    assert D(october["new_notional"]) == 0
    assert D(october["new_cof_weight"]) == D("8387500.281989387096774193548")
    inherited = result["prior_baseline_variances"]
    assert len(inherited) == 1
    assert inherited[0]["month"] == "2026-06" and inherited[0]["venue"] == "E23"
    assert D(inherited[0]["income_delta"]).quantize(D(".01")) == D("-10747.77")
    # E43's earlier missing-venue credit is not paid again by this method delta.
    e43 = next(r for r in result["results"] if r["month"] == "2026-09" and r["venue"] == "E43")
    assert D(e43["income_delta"]).quantize(D(".01")) == D("386.37")


def test_retained_capture_gap_is_not_a_zero_month(tmp_path):
    import json

    from scripts.reconcile_grove_base_cash import INPUT, replay

    source = json.loads(INPUT.read_text())
    source["chunks"].pop(1)
    altered = tmp_path / "inputs.json"
    altered.write_text(json.dumps(source))
    with pytest.raises(ValueError, match="coverage gap"):
        replay(altered)


def test_floor_boundary_uses_the_verified_e12_withdrawal(monkeypatch):
    import json
    from pathlib import Path
    from unittest.mock import patch

    from settle.extract import rpc
    from settle.normalize.positions import _uniswap_v3_value
    from settle.normalize.sources.uniswap_v3 import RPCUniswapV3PositionSource

    doc = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "reconciliation/grove-base-cash-2026/e12-withdrawal.json"
        ).read_text()
    )
    f = fixture()
    venue = next(v for v in f.prime.venues if v.id == "E12")

    def post(url, method, params):
        return doc["rpc"][json.dumps([method, params], sort_keys=True)]

    monkeypatch.setenv("SETTLE_NO_CACHE", "1")
    with (
        patch.object(rpc, "rpc_url", lambda *args: "retained:ethereum"),
        patch.object(rpc, "_post", post),
    ):
        before, after = [
            _uniswap_v3_value(f.prime, venue, b, source=RPCUniswapV3PositionSource())
            for b in [doc["before_block"], doc["after_block"]]
        ]
    assert before == D(doc["before_value"]) and after == D(doc["after_value"])
    assert before > 25000000 > after
    assert doc["date"] == "2026-09-13"
