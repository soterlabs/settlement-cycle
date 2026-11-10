"""Canonical transfers/queue receipts, not a hand-entered redemption calendar."""

import calendar
import gzip
import json
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.domain.config import load_prime_by_id
from settle.domain.period import Period
from settle.extract import hypersync_store, rpc
from settle.extract.hypersync import LogRow
from settle.normalize import async_capital as ac

ROOT = Path(__file__).resolve().parents[2] / "reconciliation/async-allocations-2026-01_to_2026-09"


def install(monkeypatch):
    h = json.load(gzip.open(ROOT / "history.json.gz"))
    q = json.load(gzip.open(ROOT / "state-quotes.json.gz"))
    rows = [LogRow(**r) for group in h["queries"].values() for r in group["rows"]]
    monkeypatch.setenv("SETTLE_NO_CACHE", "1")

    def fetch(chain, selections, start, pin, **kw):
        def match(r, s):
            return r.address in s["address"] and all(
                not t or getattr(r, "topic" + str(i)) in t
                for i, t in enumerate(s.get("topics", []))
            )

        return [
            r
            for r in rows
            if start <= r.block_number <= pin and any(match(r, s) for s in selections)
        ]

    def post(url, method, params):
        key = json.dumps([method, params], sort_keys=True, separators=(",", ":"))
        if key not in q["rpc"]:
            raise AssertionError("Unrecorded canonical RPC input")
        return q["values"][q["rpc"][key]]

    monkeypatch.setattr(rpc, "_post", post)
    monkeypatch.setattr(rpc, "rpc_url", lambda chain: "recorded")
    monkeypatch.setattr(hypersync_store, "fetch_logs", fetch)
    monkeypatch.setattr(ac, "receipt", lambda chain, tx: h["maple_return_receipts"][tx])

    class Resolver:
        def block_at_or_before(self, chain, dt):
            return q["boundaries"][dt.date().isoformat()]

    return q, Resolver()


@pytest.mark.parametrize(
    "name,vid,month,expected",
    [
        ("spark", "S14", 1, "9948.92585762339100"),
        ("spark", "S15", 4, "4161.79148017835100"),
        ("obex", "V1", 7, "6187.96807716944700"),
    ],
)
def test_canonical_owner_scoped_queues_refunds_and_payments(
    monkeypatch, name, vid, month, expected
):
    q, resolver = install(monkeypatch)
    prime = load_prime_by_id(name)
    venue = next(v for v in prime.venues if v.id == vid)
    start, end = date(2026, month, 1), date(2026, month, calendar.monthrange(2026, month)[1])
    period = Period(start, end, {venue.chain: q["boundaries"][str(end)]})
    opening = q["boundaries"][str(date.fromordinal(start.toordinal() - 1))]
    prices = {
        b: D(q["prices"][day + "/" + venue.token.address.hex]) for day, b in q["boundaries"].items()
    }
    events = ac.capital_events(
        prime,
        venue,
        period,
        opening_block=opening,
        block_resolver=resolver,
        nav_at_block=lambda b: prices[b],
    )
    assert sum((r["capital_adjustment_usd"] for r in events), D(0)) == D(expected)
    assert all(r["block"] <= period.pin_blocks[venue.chain] for r in events)


def test_ethena_complete_earlier_history_closes_to_zero_in_2026(monkeypatch):
    q, resolver = install(monkeypatch)
    prime = load_prime_by_id("spark")
    venue = next(v for v in prime.venues if v.id == "S16")
    period = Period(
        date(2026, 1, 1), date(2026, 9, 30), {venue.chain: q["boundaries"]["2026-09-30"]}
    )
    assert (
        ac.capital_events(
            prime,
            venue,
            period,
            opening_block=q["boundaries"]["2025-12-31"],
            block_resolver=resolver,
            nav_at_block=lambda b: D(1),
        )
        == []
    )


def test_manual_processing_partial_payment_and_cancellation_conserve_claim(monkeypatch):
    from settle.domain import Address
    from settle.normalize import async_vaults

    prime = load_prime_by_id("spark")
    venue = next(v for v in prime.venues if v.id == "S14")
    owner = ac.who(prime.alm[venue.chain])
    escrow = venue.async_allocation.managers[0].address
    manager = Address.from_str("0x7ad5ffa5fdf509e30186f4609c2f6269f4b6158f")
    request_id = "0x" + format(1, "064x")
    rows = []

    def add(block, address, sig, topics, amounts):
        rows.append(
            LogRow(
                block,
                len(rows),
                1767225600 + (block - 1) * 86400,
                address,
                ac.topic(sig),
                *(topics + [None] * (3 - len(topics))),
                "0x" + "".join(format(x * 10**6, "064x") for x in amounts),
                "0x" + format(block, "064x"),
            )
        )

    add(
        1,
        venue.token.address.hex,
        "Transfer(address,address,uint256)",
        [owner, ac.who(manager)],
        [100],
    )
    add(1, escrow.hex, "RequestCreated(uint256,address,uint256)", [request_id, owner], [100])
    add(1, manager.hex, "RedeemRequested(address,uint256)", [owner], [100])
    add(
        2,
        escrow.hex,
        "RequestProcessed(uint256,address,uint256,uint256)",
        [request_id, owner],
        [40, 48],
    )
    add(2, escrow.hex, "ManualSharesIncreased(uint256,address,uint256)", [request_id, owner], [40])
    add(
        3,
        venue.token.address.hex,
        "Transfer(address,address,uint256)",
        [ac.who(escrow), owner],
        [20],
    )
    add(
        3,
        venue.token.address.hex,
        "Transfer(address,address,uint256)",
        [owner, ac.ZERO_TOPIC],
        [20],
    )
    add(3, manager.hex, "RedeemProcessed(address,uint256,uint256)", [owner], [20, 24])
    add(
        3,
        venue.underlying.address.hex,
        "Transfer(address,address,uint256)",
        [ac.who(venue.token.address), owner],
        [24],
    )
    add(
        4,
        escrow.hex,
        "RequestProcessed(uint256,address,uint256,uint256)",
        [request_id, owner],
        [30, 36],
    )
    add(
        4,
        venue.underlying.address.hex,
        "Transfer(address,address,uint256)",
        [ac.who(venue.token.address), owner],
        [36],
    )
    add(
        5,
        venue.token.address.hex,
        "Transfer(address,address,uint256)",
        [ac.who(escrow), owner],
        [10],
    )

    def fetch(chain, selections, start, pin, **kw):
        return [
            r
            for r in rows
            if r.block_number <= pin
            and any(
                r.address in s["address"]
                and all(
                    not t or getattr(r, "topic" + str(i)) in t for i, t in enumerate(s["topics"])
                )
                for s in selections
            )
        ]

    monkeypatch.setattr(hypersync_store, "fetch_logs", fetch)
    monkeypatch.setattr(rpc, "is_contract_deployed", lambda *args: True)
    monkeypatch.setattr(ac, "address_view", lambda *args: manager)
    monkeypatch.setattr(
        async_vaults,
        "async_vault_exposure",
        lambda p, v, b: async_vaults.AsyncVaultExposure(
            redemption_shares=D({0: 0, 2: 100, 3: 80, 4: 50, 5: 40}[b])
        ),
    )
    proof = {
        "blockNumber": "0x5",
        "logs": [
            dict(
                address=escrow.hex,
                topics=[ac.topic("RequestDecreased(uint256,uint256)"), request_id],
                data="0x" + format(10 * 10**6, "064x"),
            )
        ],
    }
    monkeypatch.setattr(ac, "receipt", lambda *args: proof)

    class Resolver:
        def block_at_or_before(self, chain, dt):
            return dt.day

    def run(day):
        return ac.capital_events(
            prime,
            venue,
            Period(date(2026, 1, 1), date(2026, 1, day), {venue.chain: day}),
            opening_block=0,
            block_resolver=Resolver(),
            nav_at_block=lambda b: D("1.2"),
        )

    assert sum(r["capital_adjustment_usd"] for r in run(2)) == 120
    assert sum(r["capital_adjustment_usd"] for r in run(3)) == 96
    assert sum(r["capital_adjustment_usd"] for r in run(4)) == 60
    assert sum(r["capital_adjustment_usd"] for r in run(5)) == 48
    proof["logs"][0]["topics"][1] = "0x" + format(999, "064x")
    with pytest.raises(ValueError, match="owner-specific cancellation"):
        run(5)
