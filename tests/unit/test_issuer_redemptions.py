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
