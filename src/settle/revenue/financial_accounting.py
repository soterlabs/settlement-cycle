"""Project agreed reporting corrections before statement rollups.

No chain reads here. Unknown supplemental inputs remain missing rather than
being guessed from sale proceeds or a balancing residual.
"""

from copy import deepcopy

from .expense_inputs import contributor_input
from .financial import metric, number
from .financial_spark import saved_component
from .statement_rollups import combine


def extend(statement, supplements, *, expense_revision=None):
    lookup = {r["id"]: r for r in statement["lines"]}
    periods = statement["periods"]
    compensation, expense_revision = contributor_input(expense_revision)
    statement["expense_input_revision"] = expense_revision
    statement["lines"].append(
        dict(
            id="expenses.compensation.spk",
            label="Contributor token compensation",
            aggregation="leaf",
            values={
                p["key"]: (
                    {**metric(reason="future_period"), "status": "future"}
                    if p["state"] == "future"
                    else metric(
                        compensation["months"][p["key"]],
                        reason="awaiting_approved_expense_sheet",
                        basis=compensation["basis"],
                        source_paths=["contributor_compensation.json"],
                    )
                )
                for p in periods
            },
        )
    )

    for cell in statement["lines"][-1]["values"].values():
        cell["input_revision"] = statement["expense_input_revision"]
        cell["quality"] = "estimate"
        cell["recognition"] = "accrual"
        if cell.get("value") is not None:
            cell["reason"] = None
    keys = {
        "fees.sparklend.nonstable": "Nonstable SparkLend reserve fees",
        "rewards.treasury.morpho": "MORPHO rewards",
        "rewards.treasury.avax": "AVAX rewards",
    }
    for key, label in keys.items():
        row = dict(id=key, label=label, aggregation="leaf", values={})
        for p, saved in zip(periods, supplements, strict=True):
            name = "reserve_nonstable" if key.startswith("fees.") else key.rsplit(".", 1)[-1]
            family = "fees" if key.startswith("fees.") else "rewards"
            raw = (
                saved["document"].get("treasury", {}).get(family, {}).get(name, {}) if saved else {}
            )
            row["values"][p["key"]] = (
                {**metric(reason="future_period"), "status": "future"}
                if p["state"] == "future"
                else saved_component(
                    raw,
                    "value",
                    saved,
                    recognition="receipt_in_kind",
                    expected=p["expected_cutoff"],
                )
                if saved
                else metric(reason="treasury_receipts_not_published")
            )
        statement["lines"].append(row)
        lookup[key] = row
    statement["treasury_cash_disposals"] = [
        dict(r, month=p["key"], source_ref=saved["revision_id"])
        for p, saved in zip(periods, supplements, strict=True)
        if saved and saved["cutoff"] == p["expected_cutoff"]
        for r in saved["document"].get("treasury", {}).get("cash_disposals", [])
    ]
    fees = lookup["fees.sparklend"]
    fees["components"].append({"id": "fees.sparklend.nonstable", "sign": 1})
    # The old net FX presentation put depositor FX under asset gains. Move the
    # same expense to the Savings liability family; it is not another charge.
    gains = lookup.get("gains.sparklend_nonstable")
    if gains:
        gains["label"] = "SparkLend non-USD reserves"
        gains["components"] = [{"id": "revaluation.sparklend_assets", "sign": 1}]
    for key, alias, label in (
        ("revaluation.sparklend_assets", "revaluation.alm_assets", "ALM reserve revaluation"),
        ("funding.sky.net", "funding.sky.retained", "Retained Sky funding charge"),
    ):
        if key in lookup:
            row = deepcopy(lookup[key])
            row.update(id=alias, label=label)
            statement["lines"].append(row)
            lookup[alias] = row
    for key, label in (
        ("revaluation.ops_reserves", "Ops reserve revaluation"),
        ("funding.sky.sde_adjustment", "Historical SDE funding adjustment"),
    ):
        row = dict(id=key, label=label, aggregation="leaf", values={})
        statement["lines"].append(row)
        lookup[key] = row
    lookup["revaluation.sparklend_assets"]["components"] = [
        dict(id="revaluation.alm_assets", sign=1),
        dict(id="revaluation.ops_reserves", sign=1),
    ]
    lookup["funding.sky.net"]["components"] = [
        dict(id="funding.sky.retained", sign=1),
        dict(id="funding.sky.sde_adjustment", sign=1),
    ]
    liability = lookup.get("revaluation.savings_liabilities")
    if liability:
        liability["label"] = "Savings ETH liability revaluation"
        liability["funding_component"] = True
        funding = lookup["funding.savings"]
        funding["components"].append({"id": liability["id"], "sign": -1})
    for p, saved in zip(periods, supplements, strict=True):
        month = p["key"]
        if p["state"] == "future":
            for k in ("revaluation.ops_reserves", "funding.sky.sde_adjustment"):
                lookup[k]["values"][month] = {**metric(reason="future_period"), "status": "future"}
            continue
        treasury = saved["document"].get("treasury", {}) if saved else {}
        for kind in ("morpho", "avax"):
            raw = treasury.get("gains", {}).get(kind, {})
            lookup["gains." + kind]["values"][month] = (
                saved_component(raw, "value", saved, expected=p["expected_cutoff"])
                if saved
                else metric(reason="full_cluster_treasury_gains_not_published")
            )
        extra = treasury.get("gains", {}).get("reserve_nonstable", {})
        asset = lookup.get("revaluation.sparklend_assets")
        if asset:
            extra_cell = (
                saved_component(extra, "value", saved, expected=p["expected_cutoff"])
                if saved
                else metric(reason="treasury_gains_not_published")
            )
            lookup["revaluation.ops_reserves"]["values"][month] = extra_cell
            asset["values"][month] = combine(
                [
                    (
                        "revaluation.alm_assets",
                        1,
                        lookup["revaluation.alm_assets"]["values"][month],
                    ),
                    ("revaluation.ops_reserves", 1, extra_cell),
                ]
            )
        if gains and asset:
            gains["values"][month] = deepcopy(asset["values"][month])
        if liability:
            funding = lookup["funding.savings"]
            original = funding["values"][month]
            funding["values"][month] = combine(
                [
                    ("savings_interest", 1, original),
                    (liability["id"], -1, liability["values"][month]),
                ]
            )
        ownership = saved["document"].get("ownership", {}) if saved else {}
        for venue, metric_id in (
            ("S24", "investment.amm.curve.susds_usdt"),
            ("S62", "investment.amm.uniswap_v4.usdt_usds"),
        ):
            raw = ownership.get("venues", {}).get(venue, {})
            # Legacy data may have the wrong ownership; never certify it silently.
            lookup[metric_id]["values"][month] = (
                saved_component(
                    raw,
                    "value",
                    saved,
                    recognition="investment_return",
                    expected=p["expected_cutoff"],
                )
                if saved
                else metric(reason="historical_SDE_ownership_not_published")
            )
            if raw.get("status") == "available":
                lookup[metric_id]["values"][month]["allocation_split"] = {
                    **{k: raw[k] for k in ("gross_income_usd", "sky_income_usd")},
                    "prime_income_usd": raw["value"],
                    "sky_share": str(
                        number(raw["sky_income_usd"]) / number(raw["gross_income_usd"])
                    )
                    if number(raw["gross_income_usd"])
                    else "0",
                    "sky_share_basis": "effective_dated_designated_exposure",
                    "rounding_delta_usd": "0",
                }
        correction = ownership.get("funding_adjustment", {})
        adjustment = (
            saved_component(correction, "value", saved, expected=p["expected_cutoff"])
            if saved
            else metric(reason="historical_SDE_funding_not_published")
        )
        lookup["funding.sky.sde_adjustment"]["values"][month] = adjustment
        sky_funding = lookup["funding.sky.net"]
        sky_funding["values"][month] = combine(
            [
                ("funding.sky.retained", 1, lookup["funding.sky.retained"]["values"][month]),
                ("funding.sky.sde_adjustment", 1, adjustment),
            ]
        )
        fees["values"][month] = combine(
            [(c["id"], c["sign"], lookup[c["id"]]["values"][month]) for c in fees["components"]]
        )
    statement["accounting_rules"] = "src/settle/revenue/ACCOUNTING_RULES.md"
    statement["pending_expense_inputs"] = [
        dict(
            id="expenses.compensation.spk",
            reason="Contributor SPK monthly USD compensation awaits approved expense sheet",
        )
    ]


def attach_rows(statement):
    rows = statement["statement_rows"]
    # API owns indentation; expense components are supplied separately.
    rows[:] = [r for r in rows if r["metric_id"] != "revaluation.savings_liabilities"]
    for parent, keys in (
        ("protocol", ["fees.sparklend.nonstable"]),
        ("rewards", ["rewards.treasury.morpho", "rewards.treasury.avax"]),
    ):
        host = next((r for r in rows if r["id"] == parent), None)
        if not host:
            continue
        idx = rows.index(host) + 1
        lookup = {r["id"]: r for r in statement["lines"]}
        rows[idx:idx] = [
            dict(
                id=k,
                label=lookup[k]["label"],
                parent=parent,
                depth=host["depth"] + 1,
                kind=None,
                metric_id=k,
            )
            for k in keys
        ]
    host = next((r for r in rows if r["metric_id"] == "funding.savings"), None)
    if host:
        rows.insert(
            rows.index(host) + 1,
            dict(
                id="revaluation.savings_liabilities",
                label="Savings ETH liability revaluation",
                parent=host["id"],
                depth=host["depth"] + 1,
                kind=None,
                metric_id="revaluation.savings_liabilities",
            ),
        )
