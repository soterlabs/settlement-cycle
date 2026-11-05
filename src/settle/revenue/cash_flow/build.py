"""Compose a frozen month from cash events and independently sourced accruals."""

import calendar
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext

from .. import financial_store, spark_reporting_store
from ..financial import statements
from ..monthly_reports import select as monthly_select
from ..monthly_reports import table
from ..verification import digest
from .classify import cash_metrics, collect_ledger
from .config import expenses, load
from .model import amount, metric, subtotals, total, validate


def income_report(conn, cutoff):
    rows = financial_store.year_rows(conn, "spark", cutoff.year, include_spark_fees=True)
    components = spark_reporting_store.year_rows(conn, cutoff.year)
    return statements(
        "spark",
        cutoff.year,
        cutoff,
        rows,
        monthly=True,
        rollups=True,
        rwa=True,
        allocation_rewards=True,
        offchain=True,
        archive=True,
        spark_income=True,
        spark_publications=components,
        spark_amms=True,
        spark_statement=True,
    )


def expense_inputs(month):
    source = expenses()
    rows = source["lines"]
    leaves = [
        r
        for r in rows
        if r["id"] != "expenses.total" and not any(c["parent"] == r["id"] for c in rows)
    ]
    if any(r["values"].get(month) is None for r in leaves):
        return None, None, None, digest(source)
    with localcontext() as ctx:
        ctx.prec = 100
        token = sum(
            (Decimal(r["values"][month]) for r in leaves if r["id"] == "expenses.prime_token"),
            Decimal(0),
        )
        all_costs = sum((Decimal(r["values"][month]) for r in leaves), Decimal(0))
    return all_costs, all_costs - token, token, digest(source)


def build(evidence, income, *, config=None):
    config = config or load()
    cutoff = date.fromisoformat(evidence["cutoff"])
    month = str(cutoff)[:7]
    from .evidence import validate_evidence

    validate_evidence(evidence, config)
    ledger = collect_ledger(evidence, config=config)
    metrics = cash_metrics(evidence, ledger, config)
    income_lines = {r["id"]: r for r in income["lines"]}
    period = next((p for p in income["periods"] if p["key"] == month), {})

    def source(key):
        c = deepcopy(
            income_lines.get(key, {})
            .get("values", {})
            .get(month, metric(reason="Income source is unavailable"))
        )
        if period.get("actual_cutoff") != str(cutoff):
            return metric(
                reason="Income publication cutoff differs from the cash-flow cutoff; not paired with full-month expenses"
            )
        c.setdefault("source_paths", [])
        c["source_paths"] = [
            *c["source_paths"],
            "income-report:" + income["report_hash"] + ":" + key,
        ]
        return c

    all_costs, cash_costs, spk, expense_revision = expense_inputs(month)
    if cutoff.day != calendar.monthrange(cutoff.year, cutoff.month)[1]:
        # The supplied expenses are full-month provisional figures, not daily
        # observations. Never pair them with an MTD cash/income interval.
        all_costs = cash_costs = spk = None
    earned = source("statement.net_revenue")
    cost = metric(
        all_costs,
        quality="estimate",
        recognition="accrual",
        reason="Approved provisional expense snapshot",
        refs=["expense:" + expense_revision],
    )
    # Net income is never reported as revenue minus an absent expense of zero.
    metrics["net-income"] = (
        total([("statement.net_revenue", 1, earned), ("total-expenses", -1, cost)])
        if amount(earned) is not None and all_costs is not None
        else metric(reason="Revenue or expense coverage missing")
    )
    metrics["expenses-booked"] = metric(
        cash_costs,
        quality="estimate",
        recognition="accrual",
        reason="All approved cash-payable noninterest expenses; excludes SPK and interest",
        refs=["expense:" + expense_revision],
    )
    metrics["token-incentives"] = metric(
        spk,
        quality="estimate",
        recognition="accrual",
        reason="Same SPK expense as net income, added back once",
        refs=["expense:" + expense_revision],
    )
    gain = source("statement.gains")
    metrics["non-stable-gains"] = metric(
        -amount(gain) if amount(gain) is not None else None,
        complete=gain.get("status") == "available",
        quality=gain.get("quality", "estimate"),
        recognition="accrual",
        reason="Reverse income-statement noncash gains; broader treasury disposal bridge remains unverified",
        refs=gain.get("source_paths", []),
    )
    metrics["non-stable-transfers"] = metric(
        reason="Full-cluster weighted-average nonstable receipt/disposal cost basis not yet reconciled; no zero assumed"
    )
    savings = source("funding.savings")
    metrics["other-non-cash"] = metric(
        amount(savings),
        complete=False,
        quality=savings.get("quality", "estimate"),
        recognition="accrual",
        reason="Known depositor interest accrual add-back; other full-cluster noncash adjustments are unresolved",
        refs=savings.get("source_paths", []),
    )
    published = monthly_select("spark", cutoff.year, cutoff)[cutoff.month - 1]
    if published:
        raw = (
            published["raw_summary"]
            .split("#### Supply-Side revenue\n", 1)[1]
            .split("\n#####", 1)[0]
        )
        supply = table(raw)["supply-side revenue"]
        demand = Decimal(published["metrics"]["demand.total"]["value"])
        metrics["earned-not-received"] = metric(
            -(supply + demand),
            complete=False,
            quality="estimate",
            recognition="accrual",
            reason="Retained monthly entitlement; execution true-ups and wider income-scope bridge require reconciliation",
            refs=[published["source"]["url"]],
        )
    else:
        metrics["earned-not-received"] = metric(
            reason="Reviewed monthly MSC entitlement not published; cash receipts are independent"
        )
    subtotals(metrics)
    metrics["reconciliation-gap"] = total(
        [
            ("measured-change", 1, metrics["measured-change"]),
            ("net-cash-change", -1, metrics["net-cash-change"]),
        ]
    )
    metrics["cumulative-gap"] = metric(reason="Computed by the annual projection over monthly gaps")
    metrics["msc-receivable"] = metric(
        reason="Opening receivable plus earned less actual Sky receipts is computed by annual projection"
    )
    with localcontext() as ctx:
        ctx.prec = 100
        metrics["gross-allocation-cash"] = metric(
            sum(
                (
                    Decimal(r["amount_usd"])
                    for r in ledger["entries"]
                    if r["line_id"] == "allocation-changes"
                    or (
                        r["line_id"] == "unclassified"
                        and r["reason"].startswith("Allocation redemption")
                    )
                ),
                Decimal(0),
            ),
            complete=False,
            reason="Observed gross allocation cash; principal/yield attribution incomplete",
            refs=["event_ledger.gross-allocation-cash"],
        )
    summaries = [
        {
            "chain": ch,
            "events": len(s.get("events", [])),
            "status": s["status"],
            "pins": s.get("pins"),
            "balance_count": len(s.get("balances", [])),
        }
        for ch, s in sorted(evidence["chains"].items())
    ]
    entries = ledger["entries"]
    document = {
        "schema_version": "cash-flow-month.v1",
        "policy_version": config["policy"],
        "prime": "spark",
        "period_start": month + "-01",
        "cutoff": str(cutoff),
        "configuration_revision": config["revision"],
        "metrics": metrics,
        "event_summaries": summaries,
        "event_digest": digest(summaries),
        "cash_event_digest": digest(entries),
        "cash_event_count": len(entries),
        "dependencies": {
            "income_report_hash": income["report_hash"],
            "income_selection": income["selection"],
            "expense_revision": expense_revision,
            "income_actual_cutoff": period.get("actual_cutoff"),
        },
        "coverage": {
            "missing_chains": [
                ch for ch, s in evidence["chains"].items() if s["status"] == "unavailable"
            ],
            "partial_chains": [
                ch for ch, s in evidence["chains"].items() if s["status"] == "partial"
            ],
            "token_registry_gaps": ["Robinhood USDC has no reviewed token registry entry"],
            "savings_failures": ledger["savings"]["failures"],
            "unclassified_events": sum(r["line_id"] == "unclassified" for r in entries),
            "classification_complete": False,
        },
        "balance_witnesses": {
            ch: {"pins": s.get("pins"), "balances": s.get("balances", [])}
            for ch, s in evidence["chains"].items()
        },
        "noncash_note": "MSC debt additions, custody transfers and noncash position movements are excluded from cash receipts. Nonstable cost basis, Sky-direct funding and bridge claims remain separately unresolved.",
    }
    return validate(document), entries
