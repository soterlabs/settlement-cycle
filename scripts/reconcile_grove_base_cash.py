"""Offline before/after comparison of Grove's synchronous Base vault capital.

No monthly settlement, configuration booking, publication or payment. Replays
exact cash and the preceding EOD-NAV method against identical pinned inputs.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import socket
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal as D
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch

from settle.compute.prime_agent_revenue import (
    VenueRevenueInputs,
    _time_weighted_notional,
    compute_venue_revenue,
)
from settle.domain import Address, Chain, Period
from settle.domain.config import load_prime_by_id
from settle.domain.primes import NotionalScheduleEntry
from settle.extract import rpc
from settle.extract.hypersync import LogRow
from settle.normalize.erc4626_cash import cash_inflow_timeseries, share_value_inflow_timeseries
from settle.normalize.positions import _shares_to_usd_inflow_timeseries
from settle.normalize.sources.hypersync_balances import HyperSyncBalanceSource
from settle.revenue.verification import canonical

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "reconciliation/grove-base-cash-2026/inputs.json"


def replay(path=INPUT):
    doc = json.loads(path.read_text())
    if doc["status"] != "complete":
        raise ValueError("Incomplete source capture")
    periods = sorted(doc["periods"], key=lambda x: x["month"])
    if [r["month"] for r in periods] != [f"2026-{m:02}" for m in range(1, 11)]:
        raise ValueError("Expected every January-October period exactly once")
    if any(a["closing"] != b["opening"] for a, b in pairwise(periods)):
        raise ValueError("Monthly pins are not continuous")
    lower, upper = min(r["opening"] for r in periods), max(r["closing"] for r in periods)
    cursor = lower + 1
    for chunk in sorted(doc["chunks"], key=lambda x: x["opening"]):
        if chunk["opening"] != cursor or chunk["archive_height"] < chunk["closing"]:
            raise ValueError("Source coverage gap")
        cursor = chunk["closing"] + 1
    if cursor != upper + 1:
        raise ValueError("Source coverage missing period end")
    rows = [LogRow(**r) for r in doc["logs"]]
    if sum(c["count"] for c in doc["chunks"]) != len(rows):
        raise ValueError("Source capture count differs")
    if len({(r.block_number, r.log_index) for r in rows}) != len(rows):
        raise ValueError("Duplicate retained log")
    prime = load_prime_by_id("grove")
    if doc["chain"] != "base" or doc["holder"] != prime.alm[Chain.BASE].hex:
        raise ValueError("Retained source owner or chain changed")

    def covers(selection, source):
        if not set(selection["address"]).issubset(source["address"]):
            return False
        for i, allowed in enumerate(source.get("topics", [])):
            selected = (
                selection.get("topics", [])[i] if i < len(selection.get("topics", [])) else []
            )
            if allowed and (not selected or not set(selected).issubset(allowed)):
                return False
        return True

    def post(url, method, params):
        key = json.dumps([method, params], sort_keys=True)
        if key not in doc["rpc"]:
            raise ValueError("Missing pinned RPC witness")
        return doc["rpc"][key]

    def fetch(chain, sels, start, end, **kwargs):
        if chain != "base" or not lower < start <= end <= upper:
            raise ValueError("Uncovered event interval")
        if any(not any(covers(s, source) for source in doc["selections"]) for s in sels):
            raise ValueError("Uncovered event selection")
        return [
            r
            for r in rows
            if start <= r.block_number <= end
            and any(
                r.address in s["address"]
                and all(
                    not t or getattr(r, "topic" + str(i)) in t
                    for i, t in enumerate(s.get("topics", []))
                )
                for s in sels
            )
        ]

    class Resolver:
        def block_at_or_before(self, chain, stamp):
            if chain != "base":
                raise ValueError("Unexpected NAV source chain")
            target = int(stamp.timestamp())
            block = doc["boundaries"][str(target)]
            if (
                not rpc.block_timestamp(Chain.BASE, block)
                <= target
                < rpc.block_timestamp(Chain.BASE, block + 1)
            ):
                raise ValueError("NAV witness is not the exact daily boundary")
            return block

    def denied(*args, **kwargs):
        raise AssertionError("Offline comparison attempted network")

    records = []
    with (
        patch.dict(os.environ, {"SETTLE_NO_CACHE": "1", "SETTLE_REQUIRE_POSTGRES": "0"}),
        patch.object(socket.socket, "connect", denied),
        patch.object(rpc, "rpc_url", lambda *args: "retained:base"),
        patch.object(rpc, "_post", post),
    ):
        src = HyperSyncBalanceSource(
            fetch_logs=fetch,
            resolve_start_block=lambda *args: lower + 1,
            decimals_of=lambda *args: 18,
        )
        for r in periods:
            year, month = map(int, r["month"].split("-"))
            period = Period(
                date(year, month, 1),
                date(year, month, calendar.monthrange(year, month)[1]),
                {Chain.BASE: r["closing"]},
            )
            for block, day in [
                (r["opening"], period.start - timedelta(days=1)),
                (r["closing"], period.end),
            ]:
                target = int(datetime.combine(day, time.max, UTC).timestamp())
                if (
                    not rpc.block_timestamp(Chain.BASE, block)
                    <= target
                    < rpc.block_timestamp(Chain.BASE, block + 1)
                ):
                    raise ValueError("Monthly boundary is not the final block")
            for vid in ["E19", "E23", "E43"]:
                venue = next(v for v in prime.venues if v.id == vid)

                def bal(chain, token, holder, block):
                    return rpc.balance_of(Chain(chain), Address(token), Address(holder), block)

                def price(block, venue=venue):
                    raw = rpc.eth_call(
                        Chain.BASE,
                        venue.token.address,
                        "0x07a2d13a" + format(10**18, "064x"),
                        block,
                    )
                    if len(raw) != 66:
                        raise ValueError("Unpriced nonzero holding")
                    return D(int(raw, 16)) / 10**6

                values = []
                for block in [r["opening"], r["closing"]]:
                    shares = bal(
                        "base", venue.token.address.value, prime.alm[Chain.BASE].value, block
                    )
                    values.append(D(shares) / 10**18 * price(block) if shares else D(0))
                old_flows = _shares_to_usd_inflow_timeseries(
                    prime,
                    venue,
                    period,
                    balance_source=src,
                    block_resolver=Resolver(),
                    price_at_block=price,
                    som_block=r["opening"],
                    balance_at=bal,
                    period_only=True,
                )
                new_flows = cash_inflow_timeseries(
                    prime, venue, period, opening_block=r["opening"], balance_at=bal, fetch=fetch
                )

                def compute(flows, cash=None, period=period, venue=venue, values=values):
                    return compute_venue_revenue(
                        period,
                        VenueRevenueInputs(
                            venue=venue,
                            value_som=values[0],
                            value_eom=values[1],
                            inflow_timeseries=flows,
                            erc4626_period_inflow=cash,
                        ),
                    )

                average_flows = share_value_inflow_timeseries(
                    new_flows,
                    venue,
                    block_resolver=Resolver(),
                    price_at_block=price,
                )
                old = compute(old_flows)
                new = compute(average_flows, sum(new_flows["daily_inflow"], D(0)))
                if old.tw_avg_value != new.tw_avg_value:
                    raise ValueError(
                        "Cash revenue correction changed the existing exposure average"
                    )
                published = next((v for v in r["baseline_venues"] if v["venue_id"] == vid), None)
                records.append(
                    dict(
                        month=r["month"],
                        venue=vid,
                        opening_block=r["opening"],
                        closing_block=r["closing"],
                        old=canonical(old),
                        new=canonical(new),
                        income_delta=str(new.revenue - old.revenue),
                        average_delta=str(new.tw_avg_value - old.tw_avg_value),
                        global_cof_delta="0",
                        published_income=published["actual_revenue"] if published else None,
                        old_method_minus_published=(
                            str(old.revenue - D(published["revenue"])) if published else None
                        ),
                        cash_flows=canonical(new_flows.to_dict("records")),
                    )
                )
        e12 = next(v for v in prime.venues if v.id == "E12")
        original = replace(
            e12, notional_principal_usd=(NotionalScheduleEntry(date(2026, 1, 1), D(25000000)),)
        )
        weights = []
        for r in doc["e12_baselines"]:
            month = next(p for p in periods if p["month"] == r["month"])
            start = date.fromisoformat(r["month"] + "-01")
            end = datetime.fromtimestamp(
                rpc.block_timestamp(Chain.BASE, month["closing"]), UTC
            ).date()
            period = Period(start, end, {Chain.BASE: month["closing"]})
            avg = D(r["venue"].get("tw_avg_value_usd", r["venue"].get("tw_avg_value")))
            prior, corrected = (
                _time_weighted_notional(original.notional_principal_usd, period),
                _time_weighted_notional(e12.notional_principal_usd, period),
            )
            weights.append(
                dict(
                    month=r["month"],
                    measured_average=str(avg),
                    old_notional=str(prior),
                    new_notional=str(corrected),
                    old_cof_weight=str(max(avg, prior)),
                    new_cof_weight=str(max(avg, corrected)),
                    global_cof_delta="0",
                )
            )
    totals = {
        period: str(
            sum(
                (
                    D(r["income_delta"])
                    for r in records
                    if (r["month"] <= "2026-09") == (period == "jan_sep")
                ),
                D(0),
            )
        )
        for period in ["jan_sep", "october"]
    }
    return dict(
        method="exact_erc4626_cash_capital_vs_previous_daily_nav",
        input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        grove_config_sha256=hashlib.sha256((ROOT / "config/grove.yaml").read_bytes()).hexdigest(),
        e12_withdrawal_sha256=hashlib.sha256(
            (path.parent / "e12-withdrawal.json").read_bytes()
        ).hexdigest(),
        results=records,
        income_delta=totals,
        e12_weights=weights,
        global_cof_delta="0",
        scope="Incremental cash-method change only; do not add the E43 missing-venue correction twice.",
        prior_baseline_variances=[
            dict(month=r["month"], venue=r["venue"], income_delta=r["old_method_minus_published"])
            for r in records
            if r["old_method_minus_published"] is not None
            and abs(D(r["old_method_minus_published"])) > D("0.01")
        ],
        booking_status="Component comparison only; full frozen-report/prior-payment netting belongs in the combined reconciliation.",
        implementation_sha256={
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
            for p in [
                "src/settle/normalize/erc4626_cash.py",
                "src/settle/compute/prime_agent_revenue.py",
                "src/settle/normalize/positions.py",
                "scripts/reconcile_grove_base_cash.py",
            ]
        },
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.resolve().is_relative_to(
        ROOT / "settlements"
    ) or args.output.resolve().is_relative_to(ROOT / "config"):
        p.error("Use a separate research/reconciliation output path")
    result = replay()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["income_delta"], indent=2))


if __name__ == "__main__":
    main()
