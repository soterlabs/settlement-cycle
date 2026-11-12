"""Replay PR #280 only for Grove's reviewed October close.

Recompute three Base venues and E12's notional with production components.
Reuse the verified unchanged October inputs, validate the full interest/DR
rollups, and render new canonical Grove artifacts. Never calculate other primes.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import socket
from dataclasses import MISSING, fields, replace
from decimal import Decimal as D
from pathlib import Path
from runpy import run_path
from unittest.mock import patch

from settle.compute.prime_agent_revenue import (
    VenueRevenueInputs,
    _time_weighted_notional,
    compute_venue_revenue,
)
from settle.domain import Address, Chain
from settle.domain.config import load_prime_by_id
from settle.domain.monthly_pnl import MonthlyPnL, VenueRevenue
from settle.extract import rpc
from settle.extract.hypersync import LogRow
from settle.load.dr_rewards import load_dr
from settle.load.provenance import render_provenance
from settle.load.writer import write_settlement
from settle.normalize.erc4626_cash import cash_inflow_timeseries, share_value_inflow_timeseries
from settle.revenue.monthly import _decode, validate_interest
from settle.revenue.verification import canonical

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "reconciliation/2026-10/grove-pr280"


def decode_provenance(prov):
    def venue(row, *, reporting=False):
        aliases = {"tw_avg_value": "tw_avg_value_usd", "tw_avg_notional": "tw_avg_notional_usd"}
        values = {}
        for f in fields(VenueRevenue):
            key = aliases.get(f.name, f.name)
            if key in row:
                values[f.name] = row[key]
            elif f.name == "incentive_accrual":
                values[f.name] = {}
            elif reporting and f.default is not MISSING:
                values[f.name] = canonical(f.default)
            elif reporting and f.default_factory is not MISSING:
                values[f.name] = canonical(f.default_factory())
            else:
                raise ValueError("Missing financial venue field " + f.name)
        return values

    values = {}
    for f in fields(MonthlyPnL):
        if f.name == "month":
            values[f.name] = {"year": 2026, "month": 10}
        elif f.name == "period":
            values[f.name] = {
                "start": prov["period"]["start"],
                "end": prov["period"]["end"],
                "pin_blocks": prov["pin_blocks_eom"],
            }
        elif f.name in ["venue_breakdown", "reporting_only_breakdown"]:
            values[f.name] = [
                venue(v, reporting=f.name == "reporting_only_breakdown") for v in prov[f.name]
            ]
        elif f.name == "allocation_financing":
            values[f.name] = prov.get(f.name)
        elif f.name in prov:
            values[f.name] = prov[f.name]
        elif f.name in prov["results"]:
            values[f.name] = prov["results"][f.name]
        else:
            raise ValueError("Missing monthly field " + f.name)
    return _decode(MonthlyPnL, values)


def replay(output):
    review_hash = run_path(str(ROOT / "scripts/run_october_close.py"))["check_review"]("grove")
    baseline_path = FOLDER / "before-provenance.json"
    base = json.loads(baseline_path.read_text())
    old_preview = json.loads((FOLDER / "before-preview.json").read_text())
    for path, key in [
        (FOLDER / "before-summary.md", "summary_sha256"),
        (FOLDER / "before-workbook.xlsx", "workbook_sha256"),
    ]:
        if hashlib.sha256(path.read_bytes()).hexdigest() != old_preview[key]:
            raise ValueError("October report baseline hash differs")
    before = decode_provenance(base)
    roundtrip = render_provenance(before)
    for k in [
        "results",
        "venue_breakdown",
        "reporting_only_breakdown",
        "sky_revenue_daily",
        "sde_daily_breakdown",
        "venue_views",
    ]:
        if roundtrip[k] != base[k]:
            raise ValueError("Lossy October snapshot decode: " + k)
    if before.prime_id != "grove" or str(before.month) != "2026-10":
        raise ValueError("Wrong October replay prime/period")
    if before.monthly_pnl + before.sde_revenue != D(old_preview["ordinary_prime_net"]):
        raise ValueError("Snapshot does not match committed October MSC")
    dr = load_dr("grove", "2026-10")
    if dr is None or dr["total"] != before.distribution_rewards:
        raise ValueError("Final October DR snapshot changed")
    doc = json.loads((ROOT / "reconciliation/grove-base-cash-2026/inputs.json").read_text())
    assert doc["status"] == "complete"
    prime = load_prime_by_id("grove")
    period = before.period
    states = doc["rpc"]
    logs = [LogRow(**r) for r in doc["logs"]]
    changed = {}

    def post(url, method, params):
        return states[json.dumps([method, params], sort_keys=True)]

    def fetch(chain, sels, start, end, **kw):
        if chain != "base" or start != 52011727 or end != 53350926:
            raise ValueError("Unexpected October replay interval")
        return [
            r
            for r in logs
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

    def denied(*a, **kw):
        raise AssertionError("October replay attempted network access")

    class Resolver:
        def block_at_or_before(self, chain, stamp):
            target = int(stamp.timestamp())
            b = doc["boundaries"][str(target)]
            if (
                not rpc.block_timestamp(Chain.BASE, b)
                <= target
                < rpc.block_timestamp(Chain.BASE, b + 1)
            ):
                raise ValueError("Wrong daily quote pin")
            return b

    with (
        patch.dict(os.environ, {"SETTLE_NO_CACHE": "1", "SETTLE_REQUIRE_POSTGRES": "0"}),
        patch.object(socket.socket, "connect", denied),
        patch.object(rpc, "rpc_url", lambda *a: "retained:base"),
        patch.object(rpc, "_post", post),
    ):
        for vid in ["E19", "E23", "E43"]:
            v = next(v for v in prime.venues if v.id == vid)
            old = next(r for r in before.venue_breakdown if r.venue_id == vid)

            def bal(chain, token, owner, block):
                return rpc.balance_of(Chain(chain), Address(token), Address(owner), block)

            def price(block, v=v):
                return (
                    D(
                        int(
                            rpc.eth_call(
                                Chain.BASE,
                                v.token.address,
                                "0x07a2d13a" + format(10**18, "064x"),
                                block,
                            ),
                            16,
                        )
                    )
                    / 10**6
                )

            values = []
            for b in [before.pin_blocks_som[Chain.BASE], period.pin_blocks[Chain.BASE]]:
                shares = bal("base", v.token.address.value, prime.alm[Chain.BASE].value, b)
                values.append(D(shares) / 10**18 * price(b) if shares else D(0))
            assert values == [old.value_som, old.value_eom]
            cash = cash_inflow_timeseries(
                prime,
                v,
                period,
                opening_block=before.pin_blocks_som[Chain.BASE],
                balance_at=bal,
                fetch=fetch,
            )
            averages = share_value_inflow_timeseries(
                cash, v, block_resolver=Resolver(), price_at_block=price
            )
            new = compute_venue_revenue(
                period,
                VenueRevenueInputs(
                    venue=v,
                    value_som=values[0],
                    value_eom=values[1],
                    inflow_timeseries=averages,
                    erc4626_period_inflow=sum(cash.daily_inflow, D(0)),
                ),
            )
            assert new.tw_avg_value == old.tw_avg_value and new.sd_revenue == old.sd_revenue
            changed[vid] = new
        e12 = next(v for v in prime.venues if v.id == "E12")
        old = next(r for r in before.venue_breakdown if r.venue_id == "E12")
        changed["E12"] = replace(
            old, tw_avg_notional=_time_weighted_notional(e12.notional_principal_usd, period)
        )
    venues = [changed.get(v.venue_id, v) for v in before.venue_breakdown]
    revenue = (
        sum((v.revenue for v in venues), D(0))
        + before.curve_susds_spread
        + before.psm3_susds_spread
        + before.psm3_susds_appreciation
    )
    views = copy.deepcopy(before.venue_views)
    for kind in ["settlement", "reporting"]:
        for row in views.get(kind, []):
            if row["venue_id"] in ["E19", "E23", "E43"]:
                if row["method"] != "existing":
                    raise ValueError("Cash view attribution requires review")
                row["income_usd"] = str(changed[row["venue_id"]].revenue)
    net = (
        revenue
        + before.agent_rate
        + before.distribution_rewards
        + before.chronicle_points
        + before.gar
        - before.sky_revenue
    )
    after = replace(
        before,
        venue_breakdown=venues,
        prime_agent_revenue=revenue,
        monthly_pnl=net,
        venue_views=views,
    )
    reference = json.loads((ROOT / "config/october_2026_official_reference_rates.json").read_text())
    with patch.object(socket.socket, "connect", denied):
        validate_interest(after, prime, {"reference_rates": reference})
    assert (
        after.sky_revenue == before.sky_revenue
        and after.distribution_rewards == before.distribution_rewards
    )
    paths = write_settlement(
        after,
        output,
        sources={
            "calculation": "Grove-only affected-venue replay after #280; verified unchanged October snapshot retained",
            "alm_transfer_review_sha256": review_hash,
            "previous_provenance_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            "base_commit": "703c66863a0e99edf839658081026926f038c70b",
        },
        reference_rates=reference,
    )
    written = json.loads(paths["provenance"].read_text())
    assert written["results"]["distribution_rewards"] == base["results"]["distribution_rewards"]
    import shutil
    from tempfile import TemporaryDirectory

    import yaml

    from settle.compute.sky_total_accrual import AccrualPrimeRow, _load_prime_components, _round0

    cfg = yaml.safe_load((ROOT / "config/sky_total.yaml").read_text())
    cfg["accrual_primes"] = ["grove"]
    cfg["allocator_ilks"] = {"grove": cfg["allocator_ilks"]["grove"]}
    cfg["msc_preview"] = {"2026-10": {"grove": cfg["msc_preview"]["2026-10"]["grove"]}}
    with TemporaryDirectory(prefix="grove-msc-only-") as temporary:
        root = Path(temporary)
        folder = root / "settlements/grove/2026-10"
        folder.mkdir(parents=True)
        shutil.copy2(paths["provenance"], folder / "provenance.json")
        sky, dv, sv = _load_prime_components(root, "grove", "2026-10")
        adj = cfg["msc_preview"]["2026-10"]["grove"]
        if set(adj) - {"sky_adj", "dv_adj", "sv_adj", "send_credit"}:
            raise ValueError("October Grove preview has new pins/overrides; review before deriving")
        if "grove" not in cfg["allocator_ilks"]:
            raise ValueError("October Grove must have an allocator ilk")
        sky_adj, dv_adj, sv_adj, credit = [
            D(str(adj.get(k) or 0)) for k in ["sky_adj", "dv_adj", "sv_adj", "send_credit"]
        ]
        sv_total = max(sv, D(0)) + sv_adj
        mint = sky + sky_adj + max(sv_total, D(0))
        send = dv + dv_adj + sv_total + credit
        row = AccrualPrimeRow(
            "grove",
            True,
            sky,
            dv,
            sv,
            sky_adj,
            dv_adj,
            sv_adj,
            credit,
            _round0(mint),
            _round0(send),
            mint,
            send,
        )

    instruction = canonical(row)
    return {
        "msc_instruction": instruction,
        "prime": "grove",
        "period": "2026-10",
        "replayed_venues": ["E19", "E23", "E43", "E12"],
        "other_primes_recalculated": False,
        "previous_provenance_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "review_sha256": review_hash,
        "income_delta": str(after.prime_agent_revenue - before.prime_agent_revenue),
        "before": base["results"],
        "after": written["results"],
        "settlement_payment": written["settlement_payment"],
        "output_hashes": {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in paths.items()},
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    r = replay(a.output_dir)
    (FOLDER / "replay.json").write_text(json.dumps(r, indent=2) + "\n")
    print(json.dumps(r["settlement_payment"], indent=2))


if __name__ == "__main__":
    main()
