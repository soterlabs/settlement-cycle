from decimal import Decimal as D

import pytest

from settle.domain.config import load_prime_by_id
from settle.normalize.issuer_redemptions import policy_for, replay


def target(vid):
    p = load_prime_by_id("spark")
    v = next(v for v in p.venues if v.id == vid)
    return v, policy_for(p, v)


def events(group):
    return [dict(**r, kind="request") for r in group["requests"]] + [
        {k: v for k, v in dict(**p, kind="cash").items() if k != "final"} for p in group["payments"]
    ]


def test_buidl_small_request_and_partial_advance_remain_owned():
    v, p = target("S19")
    small, large = p["groups"]
    small_events = events(small)
    pending, _, _ = replay(v, p, small_events, pin=small["requests"][0]["block"])
    assert D(next(iter(pending.values()))["fixed_value"]) == 250000
    assert replay(v, p, small_events, pin=small["payments"][0]["block"])[0] == {}
    large_events = events(large)
    pending, actions, _ = replay(v, p, large_events, pin=large["payments"][0]["block"])
    assert D(next(iter(pending.values()))["fixed_value"]) == D("199999901.456483")
    assert len(actions) == 2
    assert replay(v, p, large_events, pin=large["payments"][1]["block"])[0] == {}


def test_buidl_unassigned_receipt_does_not_invent_request():
    v, p = target("S19")
    e = {k: x for k, x in p["excluded_cash"][0].items() if k != "reason"}
    pending, actions, ignored = replay(v, p, [dict(**e, kind="cash")], pin=e["block"])
    assert not pending and not actions
    assert ignored[0]["reason"] == "reviewed_unassigned_receipt_no_request"


def test_ustb_uses_request_execution_nav_not_one_dollar():
    v, p = target("S21")
    group = p["groups"][0]
    pending, _, _ = replay(
        v, p, events(group), pin=group["requests"][0]["block"], price_at=lambda v, b: D("10.675986")
    )
    assert D(next(iter(pending.values()))["fixed_value"]) == D("106.759860")


def test_uscc_does_not_look_ahead_to_settlement_price():
    v, p = target("S22")
    group = p["groups"][0]

    def no_future(*args):
        raise AssertionError("Unpriced source-only claim requested a settlement quote")

    pending, _, _ = replay(
        v, p, events(group), pin=group["requests"][-1]["block"], price_at=no_future
    )
    assert sum(D(r["shares"]) for r in pending.values()) == D("8843655.9876")
    assert all(r["fixed_value"] is None for r in pending.values())
    pending, _, _ = replay(
        v, p, events(group), pin=group["payments"][0]["block"], price_at=lambda v, b: D("11.384682")
    )
    assert not pending


def test_receipt_without_its_request_fails():
    v, p = target("S19")
    e = events(p["groups"][0])[-1]
    with pytest.raises(ValueError, match="source request"):
        replay(v, p, [e], pin=e["block"])


def test_unknown_material_receipt_fails_and_duplicate_evidence_is_checked():
    v, p = target("S19")
    e = events(p["groups"][0])[0]
    with pytest.raises(ValueError, match="duplicate"):
        replay(v, p, [e, dict(e, amount="1")], pin=e["block"])
    with pytest.raises(ValueError, match="Unattributed"):
        replay(v, p, [dict(e, identity="0x" + "ab" * 32 + ":1", kind="cash")], pin=e["block"])


def test_common_payer_cash_for_other_fund_is_explicitly_excluded():
    v, p = target("S21")
    e = {k: x for k, x in p["excluded_cash"][0].items() if k not in {"reason", "final"}}
    pending, _, ignored = replay(v, p, [dict(**e, kind="cash")], pin=e["block"])
    assert not pending and ignored[0]["reason"] == "reviewed_other_venue_S22"


def test_small_buidl_exit_and_cash_fee_are_each_recognized_once(monkeypatch):
    from datetime import date

    import pandas as pd

    from settle.compute.prime_agent_revenue import VenueRevenueInputs, compute_venue_revenue
    from settle.domain.period import Period
    from settle.normalize import issuer_redemptions as issuer
    from settle.normalize.async_vaults import async_capital_inflow_timeseries
    from settle.normalize.redemption_settlements import restore_redemption_capital

    prime = load_prime_by_id("spark")
    v, p = target("S19")
    g = p["groups"][0]
    period = Period(date(2025, 5, 14), date(2025, 5, 14), {v.chain: g["payments"][0]["block"]})
    pending, actions, ignored = replay(v, p, events(g), pin=period.pin_blocks[v.chain])
    monkeypatch.setattr(issuer, "ledger_at", lambda *args: (pending, actions, ignored))
    ledger = issuer.settlement_ledger(prime, v, period)
    raw = pd.DataFrame(columns=["block_date", "daily_inflow", "cum_inflow"])
    restored = restore_redemption_capital(raw, ledger)
    assert sum(restored.daily_inflow) == -250000

    class Resolver:
        def block_at_or_before(self, *args):
            return period.pin_blocks[v.chain]

    capital = async_capital_inflow_timeseries(
        prime,
        v,
        period,
        restored,
        opening_block=1,
        block_resolver=Resolver(),
        nav_at_block=lambda b: D(1),
    )
    revenue = compute_venue_revenue(
        period,
        VenueRevenueInputs(
            venue=v,
            value_som=D(250000),
            value_eom=D(0),
            inflow_timeseries=capital,
            fee_inflow_timeseries=restored,
        ),
    )
    assert revenue.actual_revenue == D("-125.702631")
    assert sum(capital.daily_inflow) == D("-249874.297369")


def test_cash_payment_does_not_require_a_new_share_nav(monkeypatch):
    from datetime import date

    from settle.domain.period import Period
    from settle.normalize import issuer_redemptions as issuer

    prime = load_prime_by_id("spark")
    v, p = target("S19")
    g = p["groups"][0]
    pending, actions, ignored = replay(v, p, events(g), pin=g["payments"][0]["block"])
    monkeypatch.setattr(
        issuer,
        "ledger_at",
        lambda *args: (pending, [a for a in actions if a["kind"] == "cash"], ignored),
    )
    period = Period(date(2025, 5, 14), date(2025, 5, 14), {v.chain: g["payments"][0]["block"]})

    def unavailable(*args):
        raise AssertionError("Fixed cash unnecessarily requested a later share quote")

    changes = issuer.capital_events(
        prime, v, period, opening_block=1, block_resolver=None, nav_at_block=unavailable
    )
    assert changes[0]["capital_adjustment_usd"] == D("-249874.297369")
    assert changes[0]["nav"] is None
