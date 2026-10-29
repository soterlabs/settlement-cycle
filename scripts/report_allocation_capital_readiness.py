#!/usr/bin/env python3
"""Daily allocation capital under the operator's Sky-except-Savings assumption.

Read-only diagnostic, not a replacement for settlement accounting. Unmatched
principal receipts are assumed Sky-funded; explicit income and realized gains
are never promoted to principal on reinvestment. Savings reachability is a
conservative dependency check, NOT evidence of a particular Savings amount.
"""

import argparse
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from settle.compute.allocation_reconciliation import validate_idle_control
from settle.domain.config import load_prime_by_id
from settle.normalize.allocation_history_cache import load_history

ZERO = D(0)
EPS = D("1e-8")

# These accounts were introduced by the existing custody adapters, not by
# the revenue report. Missing a revenue row does not exempt their principal
# or prevent computing its charge. Their tokens/policies are documented in
# normalize/allocation_capital.py, normalize/allocation_basin.py,
# normalize/allocation_paxos.py and compute/grove_secondary_cash.py.
GROVE_EXTRA_LABELS = {
    "E12_PAU": "Diamond PAU Uniswap V3 AUSD/USDC",
    "E14_PAU_CASH": "Diamond PAU idle AUSD",
    "E15_PAU_CASH": "Diamond PAU idle USDC",
    "E_BUIDL_BASIN": "Diamond PAU BUIDL Basin",
    "E_JTRSY_BASIN": "Diamond PAU JTRSY Basin",
    "E_PAXOS_BRIDGE": "Paxos USDC bridge entrypoint",
}


def known_extra_policy(prime_id, venue, end):
    if prime_id != "grove" or venue not in GROVE_EXTRA_LABELS:
        return False
    # AUSD/USDC cash and the AUSD/USDC NFT pool have no idle-USDS
    # exemption. Paxos follows the already agreed deposit-EOA boundary.
    # Basin's capped, GROVE-A-only idle-USDS exemption begins September 1
    # (config/grove.yaml). Do not apply it to August or silently omit it
    # for later reports that have not supplied the attributable deduction.
    return venue not in {"E_BUIDL_BASIN", "E_JTRSY_BASIN"} or end < date(2026, 9, 1)


def coalesce(movements):
    """Net continuous same-tx custody legs (e.g. NFT collect then forwarding)."""
    by_account = {}
    for m in movements:
        old = by_account.get(m.account)
        if old is None:
            by_account[m.account] = m
        else:
            if abs(old.value_before + old.change - m.value_before) > EPS:
                raise ValueError("Discontinuous repeated account movement")
            if old.change and m.change and old.preserve_basis != m.preserve_basis:
                raise ValueError("Conflicting custody basis semantics")
            by_account[m.account] = replace(
                old,
                change=old.change + m.change,
                external_income=old.external_income + m.external_income,
                preserve_basis=old.preserve_basis or m.preserve_basis,
            )
    return tuple(by_account.values())


def capital_timeline(history, start, end):
    """Carry original principal across exchanges; exclude gains and rewards.

    Transactions already separated by the reviewed routing adapters remain
    separate. Otherwise use weighted-average source basis, like the existing
    ledger. A new unmatched receipt is capital by operator assumption, not an
    authenticated Sky draw. Debt repayments do not erase an intact allocation.
    Missing opening movements invalidate affected accounts, never seed NAV.
    """
    if end < start:
        raise ValueError("Invalid period")
    accounts = set(history.venue_accounts.values()) | {
        a for values in history.custody_accounts.values() for a in values
    }
    basis, seen_accounts, savings, issues = defaultdict(D), set(), set(), defaultdict(set)
    observed_values = defaultdict(D)
    rows, seen = {}, set()
    batches = iter(
        sorted(history.batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index))
    )
    batch = next(batches, None)
    with localcontext() as ctx:
        ctx.prec = 60
        day = start
        while day <= end:
            while batch is not None and batch.day <= day:
                b = replace(batch, movements=coalesce(batch.movements))
                batch = next(batches, None)
                if b.identity in seen:
                    raise ValueError("Duplicate capital movement")
                seen.add(b.identity)
                incoming = []
                out_value = out_basis = ZERO
                source_issues = set()
                source_savings = any(
                    op.kind == "draw" and op.amount > 0 for op in b.external_funding
                )
                for m in b.movements:
                    if any(
                        not x.is_finite() for x in (m.value_before, m.change, m.external_income)
                    ):
                        raise ValueError("Nonfinite movement")
                    if (
                        m.value_before < 0
                        or m.external_income < 0
                        or m.value_before + m.change < -EPS
                    ):
                        raise ValueError("Invalid movement value")
                    a = m.account
                    observed_values[a] = m.value_before + m.change
                    if a not in seen_accounts and m.value_before > EPS:
                        issues[a].add("missing_opening_capital")
                    seen_accounts.add(a)
                    if m.value_before == 0 and basis[a] > EPS:
                        issues[a].add("unexplained_position_reset")
                        basis[a] = ZERO
                    value = m.value_before + m.external_income
                    change = m.change - m.external_income
                    if change < 0:
                        if value <= 0 or -change > value + EPS:
                            raise ValueError("Withdrawal exceeds value")
                        released = basis[a] * min(D(1), -change / value)
                        basis[a] -= released
                        out_basis += released if m.preserve_basis else min(released, -change)
                        out_value -= change
                        source_savings |= a in savings
                        source_issues.update(issues[a])
                    elif change > 0:
                        incoming.append((a, change))
                received = sum((v for _, v in incoming), ZERO)
                carried = out_basis * min(D(1), received / out_value) if out_value else ZERO
                new_principal = max(ZERO, received - out_value)
                for a, amount in incoming:
                    basis[a] += (carried + new_principal) * amount / received
                    if source_savings:
                        savings.add(a)
                    issues[a].update(source_issues)
                # Full exits end the dependency. A later deposit is assessed
                # from its own sources; a fractional exit cannot clear it.
                for m in b.movements:
                    if m.value_before + m.change == 0:
                        basis[m.account] = ZERO
                        savings.discard(m.account)
                        issues[m.account].clear()
                if any(basis[m.account] < -EPS for m in b.movements):
                    raise ValueError("Negative principal")
            rows[str(day)] = {
                a: {
                    "principal": str(basis[a]),
                    "observed_value": str(observed_values[a]),
                    "savings_dependency": a in savings,
                    "issues": sorted(issues[a]),
                }
                for a in accounts
            }
            day += timedelta(days=1)
    return rows


def price_daily(principal, rates, idle, sde, *, excluded=False, cap_deductions=True):
    """Use existing exact daily dollar deductions, including idle USDS."""
    if set(principal) - set(rates):
        raise ValueError("Missing daily financing rate")
    total = ZERO
    for day, value in principal.items():
        amounts = [D(value), D(rates[day]), D(idle.get(day, "0")), D(sde.get(day, "0"))]
        if any(not amount.is_finite() or amount < 0 for amount in amounts):
            raise ValueError("Invalid daily principal, rate or deduction")
        exposure = amounts[0] - amounts[2] - amounts[3]
        if cap_deductions:
            exposure = max(ZERO, exposure)
        total += (ZERO if excluded else exposure) * rates[day]
    return total


def report(prime_id, history, control, idle_control, start, end):
    if control["prime_id"] != prime_id or any(
        control["period"][key] != str(value) for key, value in [("start", start), ("end", end)]
    ):
        raise ValueError("Control period or prime mismatch")
    expected_days = {str(start + timedelta(days=i)) for i in range((end - start).days + 1)}
    control_days = [r["date"] for r in control["sky_revenue_daily"]]
    if len(control_days) != len(set(control_days)) or set(control_days) != expected_days:
        raise ValueError("Settlement rates must contain exactly one row per period day")
    idle_values = {
        vid: {date.fromisoformat(day): D(value) for day, value in values.items()}
        for vid, values in idle_control["daily"].items()
    }
    if any(
        not value.is_finite() or value < 0
        for values in idle_values.values()
        for value in values.values()
    ):
        raise ValueError("Invalid daily idle deduction")
    # A source hash authenticates the control, not the extracted deduction
    # values. Reconcile those values before using them to price allocations.
    validate_idle_control(idle_values, control)
    daily = capital_timeline(history, date(2026, 1, 1), end)
    rates = {
        r["date"]: D(str(r["daily_sky_rev"])) / D(str(r["utilized"]))
        if D(str(r["utilized"])) > 0
        else D(str(r["base_apr"])) / 365
        for r in control["sky_revenue_daily"]
    }
    days = [str(start + timedelta(days=i)) for i in range((end - start).days + 1)]
    if set(days) - rates.keys():
        raise ValueError("Incomplete settlement rates")
    if any(not rates[d].is_finite() or rates[d] < 0 for d in days):
        raise ValueError("Invalid financing rate")
    revenues = {r["venue_id"]: r for r in control["venue_breakdown"]}
    sde = {
        r["venue_id"]: {x["block_date"]: x["cum_value"] for x in r["daily"]}
        for r in control.get("sde_daily_breakdown", [])
    }
    config = {v.id: v for v in load_prime_by_id(prime_id).venues}
    output = []
    for vid in dict.fromkeys([*config, *history.analytics_only_venues]):
        cfg = config.get(vid)
        accounts = (
            [history.venue_accounts[vid]] if vid in history.venue_accounts else []
        ) + history.custody_accounts.get(vid, [])
        r = {
            "venue": vid,
            "label": cfg.label
            if cfg
            else GROVE_EXTRA_LABELS.get(vid, "Additional tracked custody account"),
            "status": None,
            "cost_of_funds": None,
            "revenue": revenues.get(vid, {}).get("revenue"),
            "net_pnl": None,
        }
        if vid in history.covered_by_boundary:
            r.update(
                status="covered_by_boundary", cost_reported_under=history.covered_by_boundary[vid]
            )
        elif not accounts:
            r["status"] = (
                "savings_liability"
                if prime_id == "spark" and vid in {"S56", "S57", "S58", "S59", "S60", "S63"}
                else "income_only"
                if (prime_id == "grove" and vid == "E38")
                or (cfg and getattr(cfg, "incentive", None) is not None)
                else "missing_capital_history"
            )
        else:
            values = {d: sum((D(daily[d][a]["principal"]) for a in accounts), ZERO) for d in daily}
            deps = [d for d in days if any(daily[d][a]["savings_dependency"] for a in accounts)]
            errors = sorted({e for d in days for a in accounts for e in daily[d][a]["issues"]})
            r.update(
                daily_principal={d: str(v) for d, v in values.items()},
                average_principal=str(sum((values[d] for d in days), ZERO) / len(days)),
                closing_principal=str(values[str(end)]),
                savings_dependency_days=deps,
                measurement_issues=errors,
            )
            if accounts and all(a.startswith("facility:") for a in accounts) and not errors:
                # A cash-recognized loan's facility claim is outstanding
                # principal, unlike the temporary balance in its payment EOA.
                r["average_facility_exposure"] = str(
                    sum((D(daily[d][a]["observed_value"]) for d in days for a in accounts), ZERO)
                    / len(days)
                )
            revenue = revenues.get(vid)
            exclusion = (
                bool(revenue and revenue.get("cof_excluded"))
                or bool(cfg and cfg.cof_excluded)
                or all(a in history.idle_accounts for a in accounts)
            )
            idle = idle_control["daily"].get(vid, {})
            deduction_missing = revenue and (
                D(revenue.get("lending_idle_tw_avg_usd", "0"))
                + D(revenue.get("amm_idle_usds_tw_avg_usd", "0"))
                > 0
                and set(days) - idle.keys()
            )
            if (
                revenue
                and D(revenue.get("sd_share", "0")) > 0
                and set(days) - sde.get(vid, {}).keys()
            ):
                deduction_missing = True
            # A missing capital history remains a gap even under the assumed
            # funding policy. Likewise a Savings source is never silently Sky.
            if errors:
                r["status"] = "capital_measurement_gap"
            elif vid in history.unsupported:
                r.update(status="principal_classification_pending", detail=history.unsupported[vid])
            elif exclusion:
                r.update(status="excluded_from_cost", cost_of_funds="0")
            elif all(values[d] == 0 for d in days):
                r.update(status="no_principal_in_period", cost_of_funds="0")
            elif deps:
                r["status"] = "savings_split_pending"
            elif deduction_missing or (
                revenue is None
                and (cfg is None or cfg.sky_direct or cfg.curve_idle_usds or cfg.lending_idle_usds)
                and not known_extra_policy(prime_id, vid, end)
            ):
                r["status"] = "deduction_or_revenue_metadata_pending"
            else:
                cost = price_daily({d: values[d] for d in days}, rates, idle, sde.get(vid, {}))
                uncapped = price_daily(
                    {d: values[d] for d in days},
                    rates,
                    idle,
                    sde.get(vid, {}),
                    cap_deductions=False,
                )
                r.update(
                    status="calculable_under_assumption",
                    cost_of_funds=str(cost),
                    excess_deduction_credit=str(cost - uncapped),
                    net_pnl=str(D(revenue["revenue"]) - cost) if revenue is not None else None,
                )
            if exclusion and not errors:
                r.update(status="excluded_from_cost", cost_of_funds="0")
        output.append(r)
    return {
        "prime": prime_id,
        "start": str(start),
        "end": str(end),
        "policy": "Operator-confirmed Sky funding except Spark Savings; exclude classified income and reinvested gains.",
        "method": "Original capital carried through normalized transactions, weighted-average source basis; end-of-day convention.",
        "limitations": [
            "Assumption-based diagnostic; not a new production charge or a certified global debt reconciliation.",
            "Potential Savings dependencies propagate conservatively across mixed transactions, not quantified Savings balances.",
            "Refinancing Savings and debt repayments need separate attribution; original capital is not outstanding ilk debt.",
            f"Uses normalized history through {end} and published {start:%Y-%m} revenue/rates; no revenue regeneration.",
            "Costs reuse the existing daily effective blended rate and exact available dollar deductions.",
            "Deductions are capped at allocation principal for its charge; excess deduction credit remains explicitly separate for the global bridge.",
        ],
        "allocations": output,
    }


def markdown(results):
    month_label = date.fromisoformat(results[0]["start"]).strftime("%B %Y")
    lines = [
        f"# Allocation capital and borrowing-cost readiness — {month_label}",
        "",
        f"Daily principal history: January 1-{results[0]['end']}. Costs: {month_label}, under the operator-confirmed funding assumption.",
        "Savings dependency means a possible mixture, not proof that the entire position is Savings-funded.",
        "Reinvested gains are excluded. Published revenue, settlement amounts and API outputs are unchanged.",
        "",
        "| Prime | Venue | Allocation | Monthly average principal | Monthly borrowing cost | Status |",
        "|---|---|---|---:|---:|---|",
    ]
    for result in results:
        for r in result["allocations"]:
            average = f"${D(r['average_principal']):,.2f}" if "average_principal" in r else "—"
            cost = f"${D(r['cost_of_funds']):,.2f}" if r["cost_of_funds"] is not None else "—"
            status = r["status"].replace("_", " ")
            if "cost_reported_under" in r:
                status += ": " + r["cost_reported_under"]
            lines.append(
                f"| {result['prime']} | {r['venue']} | {r['label']} | {average} | {cost} | {status} |"
            )
    lines += [
        "",
        "## Interpretation",
        "",
        "- Calculable means conditional on the operator-confirmed funding assumption and normalized movements, not independently proven funding origin.",
        "- Principal is carried through reallocation; classified rewards and realized gains do not become borrowing. Passive NAV growth adds no principal.",
        "- Savings dependency is tested throughout the selected month, propagates through transfers, and clears on a full exit. Mixed transactions are conservative; this does not claim a Savings percentage.",
        "- EOA interiors are covered by their boundary, not charged again as independent allocations. Known Grove PAU/Basin/Paxos custody rows use their existing financing rules even without a revenue row. Missing revenue still leaves their net PnL unavailable.",
        "- The existing accepted full-principal Anchorage return policy is reused. Its remaining blocker here is the Savings split.",
        "- Exact available daily idle-USDS and SDE deductions are applied. The charge cannot be negative; excess deduction credit is recorded separately in JSON for the global reconciliation adjustment.",
        "- Zero principal and exempt USDS have zero borrowing cost. Income-only rows and Savings liabilities are not independent investment allocations.",
        "- This report does not assert that the ready subtotal equals global borrowing costs. Savings-dependent allocations, custody metadata, MSC debt and other prime-level adjustments remain outside that subtotal.",
        "",
        "## Reproduce from existing snapshots",
        "",
        "Use report_allocation_capital_readiness.py with --month, --settlements-root, both normalized histories and their matching idle-deduction files.",
        "A non-August history must carry a pins.json manifest matching the requested month and settlement block pins. Use --apply-capital-adapters for freshly normalized inputs.",
        "The compressed artifact contains daily principal, dependency days, costs, excess deduction credits, reused revenue and input/source hashes.",
    ]
    return "\n".join(lines) + "\n"


def validate_history_manifest(manifest, *, prime, end, control, history_sha256):
    """Reject a stale month's normalized positions before pricing its costs."""
    if (
        manifest.get("prime") != prime
        or manifest.get("end") != str(end)
        or manifest.get("pins") != control["pin_blocks_eom"]
        or manifest.get("history_sha256") != history_sha256
    ):
        raise ValueError("Capital history must cover the requested month at the settlement pins")


def source_files(with_adapters=False):
    paths = [
        Path(__file__),
        Path("config/spark.yaml"),
        Path("config/grove.yaml"),
        Path("src/settle/compute/spark_anchorage_correction.py"),
        Path("src/settle/compute/spark_anchorage_scenarios.py"),
    ]
    if with_adapters:
        # The dispatcher imports multiple routing/income corrections. Hashing
        # only the dispatcher would miss changes to a transitive adapter.
        paths.extend(sorted(Path("src/settle/compute").glob("*.py")))
    return paths


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--month", default="2026-08", help="Complete calendar month, YYYY-MM")
    p.add_argument("--settlements-root", type=Path, default=Path("settlements"))
    p.add_argument(
        "--apply-capital-adapters",
        action="store_true",
        help="Apply reviewed movement adapters to a fresh normalized history",
    )
    p.add_argument("--spark-history", type=Path, required=True)
    p.add_argument("--grove-history", type=Path, required=True)
    p.add_argument("--spark-idle-deductions", type=Path, required=True)
    p.add_argument("--grove-idle-deductions", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--markdown", type=Path, required=True)
    args = p.parse_args()
    from settle.revenue.allocations import month_bounds

    start, end = month_bounds(args.month)
    inputs = [args.spark_history, args.grove_history]
    for prime in ("spark", "grove"):
        inputs.extend(
            [
                args.settlements_root / prime / args.month / "provenance.json",
                getattr(args, prime + "_idle_deductions"),
            ]
        )
    if args.month != "2026-08":
        inputs.extend(
            path.parent / "pins.json" for path in (args.spark_history, args.grove_history)
        )
    if args.output.resolve() == args.markdown.resolve() or any(
        o.resolve() == i.resolve() for o in (args.output, args.markdown) for i in inputs
    ):
        p.error("Outputs must be distinct from each other and inputs")
    results = []
    for prime in ("grove", "spark"):
        path = getattr(args, prime + "_history")
        with gzip.open(path, "rt") as f:
            key = json.loads(next(f))["fingerprint"]
        history = load_history(path, key)
        if history is None:
            raise ValueError("Invalid normalized snapshot")
        if args.month != "2026-08":
            # Never silently carry an August closing position into September.
            manifest_path = path.parent / "pins.json"
            manifest = json.loads(manifest_path.read_text())
            control = json.loads(
                (args.settlements_root / prime / args.month / "provenance.json").read_text()
            )
            validate_history_manifest(
                manifest,
                prime=prime,
                end=end,
                control=control,
                history_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        if args.apply_capital_adapters:
            from settle.compute.executed_spell_capital import apply_executed_spells

            history = apply_executed_spells(history)
        if prime == "spark":
            # Reuse the accepted published full-principal policy, as in
            # audit_facility_funding --anchorage-published-principal. The
            # generic snapshot predates that policy; it is not a new decision.
            from settle.compute.spark_anchorage_correction import correct_spark_anchorage_round_trip
            from settle.compute.spark_anchorage_scenarios import apply_anchorage_scenario

            history = apply_anchorage_scenario(
                correct_spark_anchorage_round_trip(history), "all-principal"
            )
            history = replace(
                history, unsupported={k: v for k, v in history.unsupported.items() if k != "S23"}
            )
        cp = args.settlements_root / prime / args.month / "provenance.json"
        idle = json.loads(getattr(args, prime + "_idle_deductions").read_text())
        if idle["control_sha256"] != hashlib.sha256(cp.read_bytes()).hexdigest():
            raise ValueError("Idle deductions belong to a different settlement control")
        results.append(
            report(
                prime,
                history,
                json.loads(cp.read_text()),
                idle,
                start,
                end,
            )
        )
        print(prime, dict(Counter(r["status"] for r in results[-1]["allocations"])), flush=True)
        del history
    result = {
        "results": results,
        "input_sha256": {str(i): hashlib.sha256(i.read_bytes()).hexdigest() for i in inputs},
        "source_sha256": {
            str(i): hashlib.sha256(i.read_bytes()).hexdigest()
            for i in source_files(args.apply_capital_adapters)
        },
    }
    args.output.write_bytes(gzip.compress((json.dumps(result, indent=2) + "\n").encode(), mtime=0))
    args.markdown.write_text(markdown(results))


if __name__ == "__main__":
    main()
