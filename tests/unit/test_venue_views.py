from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pandas as pd
import pytest
import yaml

from settle.compute import Sources, compute_monthly_pnl
from settle.compute.venue_views import build_venue_views, loan_accrual
from settle.domain.config import _parse_view, _settlement_enabled, load_prime, load_prime_by_id
from settle.domain.monthly_pnl import MonthlyPnL, VenueRevenue
from settle.domain.period import Month, Period
from settle.domain.primes import Chain
from settle.domain.subsidy import SubsidyConfig
from settle.domain.venue_views import VenueView
from settle.normalize.positions import _cat_a_capital_inflow_timeseries
from settle.revenue.monthly import _decode
from settle.revenue.verification import canonical, digest
from tests.fixtures.mock_sources import (
    MockBalanceSource,
    MockBlockResolver,
    MockDebtSource,
    MockPositionBalanceSource,
    MockSSRSource,
)
from tests.unit.test_revenue_api import Reader, client


def terms():
    return {"opening_date": "2026-02-01", "opening_unpaid_interest_usd": "0",
            "confirmed_through": "2026-03-31", "day_count": "act/365", "source": "test signed agreement",
            "tranches": [{"id": "A", "principal_schedule": [{"date": "2026-02-01", "principal_usd": "36500"}],
                          "apr_schedule": [{"date": "2026-02-01", "apr": "0.1"}]}]}


def example(confirmed=True):
    prime = load_prime_by_id("spark")
    s23, s26 = (next(v for v in prime.venues if v.id == i) for i in ("S23", "S26"))
    if confirmed:
        s23 = replace(s23, reporting_view=VenueView(method="loan_accrual", loan_terms=terms()))
    prime = replace(prime, venues=[s23, s26])
    period = Period(date(2026, 3, 1), date(2026, 3, 3), {Chain.ETHEREUM: 200})
    rows = [VenueRevenue("S23", s23.label, D(0), D(0), D(0), D(0)),
            VenueRevenue("S26", s26.label, D(900), D(1000), D(0), D(100), actual_revenue=D(100))]
    pnl = MonthlyPnL("spark", Month(2026, 3), period, D(10), D(0), D(100), D(90),
                     rows, {Chain.ETHEREUM: 100})
    receipts = [{"source_venue": "S26", "payer": s23.settlement_view.payer, "date": d, "amount_usd": "100"}
                for d in ("2026-02-15", "2026-03-02")]
    return prime, pnl, receipts


def test_independent_accrual_cash_and_no_duplicate_income():
    prime, pnl, receipts = example()
    before = canonical(pnl)
    views = build_venue_views(prime, pnl, receipts)
    settlement, reporting = ({r["venue_id"]: r for r in views[k]} for k in ("settlement", "reporting"))
    assert settlement["S23"]["income_usd"] == "100"
    assert settlement["S26"]["income_usd"] == "0"
    assert sum(D(r["income_usd"]) for r in settlement.values()) == pnl.prime_agent_revenue
    assert reporting["S23"]["income_usd"] == "30.0"
    assert D(reporting["S23"]["opening_unpaid_income_usd"]) == 180
    assert D(reporting["S23"]["unpaid_income_usd"]) == 110
    assert D(reporting["S23"]["position_value_usd"]) == 36610
    assert reporting["S26"]["income_usd"] == "0"
    assert canonical(pnl) == before
    # Changing only reporting cannot affect settlement, even with missing terms.
    missing = replace(prime, venues=[replace(prime.venues[0], reporting_view=VenueView(
        method="loan_accrual", pending_reason="terms unconfirmed")), prime.venues[1]])
    assert build_venue_views(missing, pnl, receipts)["settlement"] == views["settlement"]


def test_missing_terms_never_fall_back_to_escrow_balance_or_cash_income():
    prime, pnl, receipts = example(confirmed=False)
    row = build_venue_views(prime, pnl, receipts)["reporting"][0]
    assert row["status"] == "unavailable" and row["reason"]
    assert row["position_value_usd"] is None and row["income_usd"] is None
    assert row["cash_received_usd"] == "100"


def test_loan_principal_rate_changes_leap_day_and_prepaid_income():
    t = terms()
    t.update(opening_date="2024-02-28", confirmed_through="2024-03-01", opening_unpaid_interest_usd="5")
    t["tranches"] = [
        {"id": "A", "principal_schedule": [{"date": "2024-02-28", "principal_usd": "36500"},
                                            {"date": "2024-02-29", "principal_usd": "18250"}],
         "apr_schedule": [{"date": "2024-02-28", "apr": "0.1"}, {"date": "2024-03-01", "apr": "0.2"}]},
        {"id": "B", "principal_schedule": [{"date": "2024-02-29", "principal_usd": "36500"}],
         "apr_schedule": [{"date": "2024-02-29", "apr": "0.1"}]}]
    result = loan_accrual(t, Period(date(2024, 2, 28), date(2024, 3, 1)),
                          [{"date": "2024-03-01", "amount_usd": "100"}])
    assert D(result["income_usd"]) == 45  # 10 + 15 + 20, fixed ACT/365 in leap years
    assert D(result["unpaid_income_usd"]) == -50
    assert len(result["daily"]) == 3
    assert D(result["principal_usd"]) == 54750
    t["day_count"] = "act/360"
    assert D(loan_accrual(t, Period(date(2024, 2, 28), date(2024, 3, 1)), [])["income_usd"]) == D(45) * 365 / 360


def test_loan_terms_expire_and_are_not_extrapolated():
    prime, pnl, receipts = example()
    t = terms()
    t["confirmed_through"] = "2026-03-02"
    prime = replace(prime, venues=[replace(prime.venues[0], reporting_view=VenueView(method="loan_accrual", loan_terms=t)), prime.venues[1]])
    assert build_venue_views(prime, pnl, receipts)["reporting"][0]["status"] == "unavailable"


@pytest.mark.parametrize("cfg,kind", [
    ({"reporting": {"enabled": "false"}}, "reporting"),
    ({"reporting": {"method": "accruall"}}, "reporting"),
    ({"reporting": {"method": "loan_accrual"}}, "reporting"),
    ({"settlement": {"method": "alm_receipts"}}, "settlement"),
    ({"reporting": {"typo": True}}, "reporting"),
])
def test_invalid_view_configuration_is_rejected(cfg, kind):
    with pytest.raises(ValueError):
        _parse_view({"id": "X", **cfg}, kind)


def test_nested_settlement_flags_preserve_legacy_validation():
    assert not _settlement_enabled({"settlement": {"enabled": False}})
    with pytest.raises(ValueError):
        _settlement_enabled({"settlement": {"enabled": False}, "settlement_enabled": True})


@pytest.mark.parametrize('change', ['duplicate_payer', 'missing_anchor', 'wrong_holder'])
def test_receipt_attribution_cannot_duplicate_or_misidentify_alm_income(config_dir, tmp_path, change):
    raw = yaml.safe_load((config_dir / 'spark.yaml').read_text())
    loan = next(v for v in raw['venues'] if v['id'] == 'S23')
    if change == 'duplicate_payer':
        duplicate = deepcopy(loan)
        duplicate['id'] = 'another-loan'
        raw['venues'].append(duplicate)
    elif change == 'missing_anchor':
        loan['settlement']['source_venue'] = 'nonexistent'
    else:
        next(v for v in raw['venues'] if v['id'] == 'S26')['holder_override'] = '0x' + 'aa' * 20
    path = tmp_path / 'spark.yaml'
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match='receipt'):
        load_prime(path)


@pytest.mark.parametrize('mutate', [
    lambda t: t.update(opening_date='20260201'),
    lambda t: t.update(opening_unpaid_interest_usd='NaN'),
    lambda t: t['tranches'][0]['apr_schedule'][0].update(apr=0.1),
    lambda t: t['tranches'][0]['principal_schedule'][0].update(principal_usd=True),
    lambda t: t['tranches'][0]['apr_schedule'][0].update(apr='invalid'),
    lambda t: t['tranches'][0]['principal_schedule'].append({'date': '2026-02-01', 'principal_usd': '0'}),
])
def test_loan_configuration_rejects_ambiguous_or_lossy_inputs(mutate):
    t = terms()
    mutate(t)
    with pytest.raises(ValueError):
        VenueView(method='loan_accrual', loan_terms=t).validate('reporting')


def test_existing_reporting_method_and_disabled_reporting_are_independent():
    prime, pnl, receipts = example()
    prime = replace(prime, venues=[replace(prime.venues[0], reporting_view=VenueView(enabled=False)), prime.venues[1]])
    views = build_venue_views(prime, pnl, receipts)
    assert views["reporting"][0]["status"] == "disabled"
    assert views["reporting"][0]["income_usd"] is None
    assert views["settlement"][0]["income_usd"] == "100"
    assert views["reporting"][1]["position_value_usd"] == views["settlement"][1]["position_value_usd"]


def test_receipt_ledger_uses_exact_capital_split_and_refund_classification():
    prime, pnl, _ = example()
    anchor = prime.venues[1]
    payer = bytes.fromhex(prime.venues[0].settlement_view.payer[2:])
    source = MockBalanceSource()
    source.cumulative_df = pd.DataFrame()
    source.inflow_by_counterparty = lambda **_: pd.DataFrame({
        "block_date": [date(2026, 3, 1)] * 4,
        "counterparty": [payer, payer, payer, bytes.fromhex("aa" * 20)],
        "signed_amount": [D(1100), D(-10), D(-5000), D(999)],
    })
    kwargs = dict(balance_source=source, external_sources={payer},
                  principal_return_overrides={payer: [(date(2026, 3, 1), D(1100), D(1000))]},
                  yield_reversal_overrides={payer: [(date(2026, 3, 1), D(10))]})
    before = _cat_a_capital_inflow_timeseries(prime, anchor, pnl.period, **kwargs)
    ledger = []
    after = _cat_a_capital_inflow_timeseries(prime, anchor, pnl.period, income_ledger=ledger, **kwargs)
    pd.testing.assert_frame_equal(before, after)
    assert [D(r["amount_usd"]) for r in ledger] == [D(100), D(-10)]


def test_orchestrator_freezes_both_views_from_the_msc_cash_classifier(monkeypatch):
    prime, _, _ = example()
    prime = replace(prime, alm={Chain.ETHEREUM: prime.alm[Chain.ETHEREUM]},
                    subproxy={Chain.ETHEREUM: prime.subproxy[Chain.ETHEREUM]},
                    psm={}, alm_start_dates={}, subsidy=SubsidyConfig(enabled=False))
    anchor = prime.venues[1]
    holder = prime.alm[Chain.ETHEREUM].value
    payer = bytes.fromhex(prime.venues[0].settlement_view.payer[2:])
    balance = MockBalanceSource()
    balance.cumulative_balance_timeseries = lambda **kw: pd.DataFrame({
        "block_date": [date(2026, 2, 28), date(2026, 3, 2)],
        "daily_net": [D(900), D(100)], "cum_balance": [D(900), D(1000)],
    }) if kw['holder'] == holder and kw['token'] == anchor.token.address.value else balance.cumulative_df
    balance.inflow_by_counterparty = lambda **kw: pd.DataFrame({
        "block_date": [date(2026, 3, 2)], "counterparty": [payer], "signed_amount": [D(100)],
    }) if kw['holder'] == holder else pd.DataFrame()
    positions = MockPositionBalanceSource()
    positions.balance_at = lambda chain, token, holder, block: (
        (900 if block == 100 else 1000) * 10**6
        if token == anchor.token.address.value and holder == prime.alm[Chain.ETHEREUM].value else 0)
    sources = Sources(
        block_resolver=MockBlockResolver(default=200), balance=balance, position_balance=positions,
        debt=MockDebtSource(pd.DataFrame({'block_date': [date(2025, 1, 1)], 'daily_dart': [0], 'cum_debt': [0]})),
        ssr=MockSSRSource(pd.DataFrame({'effective_date': [date(2026, 3, 1)], 'ssr_apy': [0.04]})),
    )
    monkeypatch.setattr('requests.Session.send', lambda *a, **kw: pytest.fail('Fixture provider call'))
    kwargs = dict(sources=sources, pin_blocks_som={Chain.ETHEREUM: 100},
                  pin_blocks_eom={Chain.ETHEREUM: 200}, as_of=date(2026, 3, 3))
    result = compute_monthly_pnl(prime, Month(2026, 3), **kwargs)
    assert result.prime_agent_revenue == 100
    assert D(result.venue_views['settlement'][0]['income_usd']) == 100
    assert D(result.venue_views['settlement'][1]['income_usd']) == 0
    assert D(result.venue_views['reporting'][0]['income_usd']) == 30
    without_reporting = replace(prime, venues=[replace(prime.venues[0], reporting_view=VenueView(enabled=False)), prime.venues[1]])
    other = compute_monthly_pnl(without_reporting, Month(2026, 3), **kwargs)
    assert other.venue_breakdown == result.venue_breakdown
    assert other.monthly_pnl == result.monthly_pnl
    assert other.sky_revenue == result.sky_revenue
    assert other.venue_views['settlement'] == result.venue_views['settlement']


def test_new_views_and_legacy_snapshot_round_trip():
    prime, pnl, receipts = example()
    saved = canonical(replace(pnl, venue_views=build_venue_views(prime, pnl, receipts)))
    assert canonical(_decode(MonthlyPnL, saved)) == saved
    saved.pop("venue_views")
    assert _decode(MonthlyPnL, saved).venue_views == {}


def record():
    prime, pnl, receipts = example()
    r = Reader()
    r.row.update(prime="spark", cutoff="2026-03-03",
                 result=canonical(replace(pnl, venue_views=build_venue_views(prime, pnl, receipts))))
    r.row["result_hash"] = digest(r.row["result"])
    return r


@pytest.mark.parametrize("view", ["settlement", "reporting"])
def test_view_api_is_read_only_versioned_and_revision_pinned(monkeypatch, view):
    r = record()
    original = deepcopy(r.row)
    monkeypatch.setattr("requests.Session.send", lambda *a, **kw: pytest.fail("API provider call"))
    c = client(r, monkeypatch)
    path = f"/v2/{view}/spark/at/2026-03-03?revision=" + "b" * 64
    response = c.get(path)
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "2.0" and body["view"] == view
    assert body["data"]["source_result_hash"] == r.row["result_hash"]
    assert body["data"]["view_hash"] == digest(body["data"]["result"])
    assert body["data"]["source_schema_version"] == "1.2"
    assert r.calls[-1][1] == {"cutoff": date(2026, 3, 3), "revision": "b" * 64}
    assert r.row == original
    assert c.get(path, headers={"If-None-Match": response.headers["etag"]}).status_code == 304
    result = body["data"]["result"]
    if view == "settlement":
        assert result["totals"]["prime_agent_revenue"] == "100"
        assert result["totals"]["prime_agent_total_revenue"] == "100"
    else:
        assert result["totals"] is None


def test_history_old_snapshots_are_unavailable_without_guessing_from_current_config(monkeypatch):
    r = record()
    old = deepcopy(r.row)
    old["result"].pop("venue_views")
    r.revenue_history = lambda *a, **kw: [r.row, old]
    response = client(r, monkeypatch).get("/v2/reporting/spark/history?start=2026-03-01&end=2026-03-03")
    assert [r["availability"] for r in response.json()["results"]] == ["available", "unavailable"]
    r.row = old
    assert client(r, monkeypatch).get("/v2/reporting/spark/latest").status_code == 409
    # The original API can still read it and labels the stored shape honestly.
    assert client(r, monkeypatch).get("/v1/revenue/spark/latest").json()["schema_version"] == "1.1"


def test_views_missing_data_failures_validation_and_catalog(monkeypatch):
    r = record()
    c = client(r, monkeypatch)
    catalog = {v["venue_id"]: v for v in c.get("/v2/venues/spark").json()["venues"]}
    assert catalog["S23"]["settlement"]["method"] == "alm_receipts"
    assert catalog["S23"]["reporting"]["method"] == "loan_accrual"
    assert catalog["S23"]["reporting"]["pending_reason"]
    assert not catalog["S69"]["settlement"]["enabled"]
    assert catalog["S69"]["reporting"]["enabled"]
    assert c.get("/v2/reporting/unknown/latest").status_code == 404
    assert c.get("/v2/reporting/spark/at/2026-09-15").status_code == 422
    assert c.get("/v2/reporting/spark/at/2026-03-03?revision=bad").status_code == 422
    assert c.get("/v2/reporting/spark/history?start=2026-01-01&end=2026-09-01").status_code == 422
    r.row = None
    assert c.get("/v2/reporting/spark/latest").status_code == 404
    def failed(*a, **kw):
        raise RuntimeError("postgres://secret")
    r.revenue_bundle = failed
    response = c.get("/v2/reporting/spark/latest")
    assert response.status_code == 503 and "secret" not in response.text
