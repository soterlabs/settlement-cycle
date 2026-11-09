"""Compose a frozen month from cash events and independently sourced accruals."""

import calendar
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext

from .. import financial_store, spark_reporting_store
from ..financial import statements
from ..verification import digest
from .classify import cash_metrics, collect_ledger
from .config import expenses, load
from .model import amount, metric, subtotals, total, validate


def income_report(conn, cutoff, *, review=False):
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
        spark_statement=True, spark_review=review,
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
    from ..financial import SPARK_REVIEW_POLICY
    from .config import REVIEW_POLICY
    income_policy = income.get("policy_version")
    if income_policy is not None and ((config["policy"] == REVIEW_POLICY) != (income_policy == SPARK_REVIEW_POLICY)):
        raise ValueError("Cash and income reporting policies use different accounting scopes")
    cutoff = date.fromisoformat(evidence["cutoff"])
    month = str(cutoff)[:7]
    from .evidence import validate_evidence

    validate_evidence(evidence, config)
    ledger = collect_ledger(evidence, config=config, disposals=[r for r in income.get("treasury_cash_disposals", []) if r["month"] == month], sde_principal=[r for r in income.get("spark_sde_cash", []) if r["month"] == month])
    metrics = cash_metrics(evidence, ledger, config)
    principal_policy = any(v.get("principal_only") for values in config["counterparties"].values() for v in values.values())
    if principal_policy and not any(r["month"] == month for r in income.get("spark_sde_cash", [])):
        ledger["sde_failures"].append("Reviewed SDE principal source is not published")
    if ledger.get("sde_failures"):
        observed = [e for e in ledger["entries"] if e["line_id"] == "sky-direct-deployments"]
        metrics["sky-direct-deployments"] = metric(sum((Decimal(e["amount_usd"]) for e in observed), Decimal(0)) if observed else None,
            complete=False, quality="estimate", reason="SDE principal evidence is incomplete; missing is not zero")
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
    from ..expense_inputs import contributor_input, INITIAL_CONTRIBUTOR_INPUT
    contributor_source, contributor_revision = contributor_input(income.get("expense_input_revision", INITIAL_CONTRIBUTOR_INPUT))
    contributor = contributor_source["months"][month]
    if contributor_source.get("schedule") and "expenses.compensation.spk" in income_lines:
        value = amount(source("expenses.compensation.spk"))
        contributor = str(value) if value is not None else None
    metrics["contributor-spk"] = metric(contributor, recognition="accrual", quality="estimate",
        reason="Provisional grant-date straight-line compensation; claim transfers are not expense timing" if contributor_source.get("schedule") else "Contributor compensation from approved monthly sheet; missing values are not zero",
        refs=["contributor_compensation.json"])
    included = expenses().get("includes_contributor_compensation", False)
    if included and cash_costs is not None:
        metrics["expenses-booked"] = (metric(cash_costs - Decimal(contributor), recognition="accrual", quality="estimate",
            reason="Sheet includes SPK; cash payable expenses exclude the same noncash grant", refs=["expense:" + expense_revision])
            if contributor is not None else metric(reason="Sheet includes noncash SPK but its value is unavailable"))
    if amount(metrics["net-income"]) is not None and not included:
        base = metrics["net-income"]
        metrics["net-income"] = total([
            ("net_income_before_contributor_SPK", 1, base),
            ("contributor-spk", -1, metrics["contributor-spk"]),
        ], reason="Contributor SPK compensation awaits updated expense sheet")
    issuer_earned = total([(key, 1, source(key)) for key in (
        "rewards.issuer.rlusd", "rewards.issuer.pyusd", "rewards.issuer.usdg")])
    metrics["issuer-rewards-noncash"] = metric(
        -amount(issuer_earned) if amount(issuer_earned) is not None else None,
        complete=issuer_earned["status"] == "available", recognition="accrual",
        quality=issuer_earned["quality"], reason="Issuer income earned in ALM allocation tokens, not cash; reverse once",
        refs=issuer_earned["source_paths"])
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
    treasury_receipts = total([(key, 1, source(key)) for key in (
        "fees.sparklend.nonstable", "rewards.treasury.morpho", "rewards.treasury.avax")])
    metrics["non-stable-transfers"] = metric(
        -amount(treasury_receipts) if amount(treasury_receipts) is not None else None,
        complete=treasury_receipts["status"] == "available", recognition="accrual",
        quality=treasury_receipts["quality"], reason="Reverse noncash treasury fee and reward receipts once",
        refs=treasury_receipts["source_paths"])
    savings = source("funding.savings")
    metrics["other-non-cash"] = metric(
        amount(savings),
        complete=False,
        quality=savings.get("quality", "estimate"),
        recognition="accrual",
        reason="Known depositor interest accrual add-back; other full-cluster noncash adjustments are unresolved",
        refs=savings.get("source_paths", []),
    )
    # Gross allocation revenue is not a Sky receivable. Use approved net
    # entitlements and book separately approved true-ups on their execution date.
    from .receivable import entitlement

    approved = entitlement(cutoff)
    metrics["earned-not-received"] = metric(
        -amount(approved) if amount(approved) is not None else None,
        complete=False,
        quality="estimate",
        recognition="accrual",
        refs=approved["source_paths"],
        reason="Known approved net MSC entitlement/true-ups; full net-income receivable bridge remains incomplete",
    )
    metrics["earned-not-received"]["approved_msc_component"] = approved
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
                    or r["id"].endswith(":vault-interest")
                    or (
                        r["line_id"] == "unclassified"
                        and r["reason"].startswith("Allocation redemption")
                    )
                ),
                Decimal(0),
            )
            if amount(metrics["allocation-changes"]) is not None
            else None,
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
            "accuracy_source_hash": digest(evidence.get("accuracy")),
            "income_selection": income["selection"],
            "expense_revision": expense_revision,
            "contributor_compensation_revision": contributor_revision,
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
            "sde_principal_failures": ledger.get("sde_failures", []),
            "allocation_basis_failures": ledger["allocation_failures"],
            "verified_redemptions": ledger["verified_redemptions"],
        },
        "balance_witnesses": {
            ch: {"pins": s.get("pins"), "balances": s.get("balances", [])}
            for ch, s in evidence["chains"].items()
        },
        "noncash_note": "MSC debt additions, custody transfers and noncash position movements are excluded from cash receipts. Nonstable cost basis, Sky-direct funding and bridge claims remain separately unresolved.",
    }
    if income.get("review_policy"):
        witnesses = [r for r in income.get("spark_sde_cash", []) if r["month"] == month]
        document["sde_principal_witnesses"] = witnesses
        document["dependencies"]["sde_principal_hash"] = digest(witnesses)
    return validate(document), entries
