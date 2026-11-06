"""Spark v3.10: explicit PSM3 income, net FX components and an honest MSC bridge.

Pure projection of retained sources. No provider calls, MSC writes, guessed
loan accruals or balancing plugs. The reviewed evidence snapshot is immutable
and belongs to this policy; changing it requires a new statement policy.
"""

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, localcontext
from functools import lru_cache
from importlib.resources import files

from .financial import metric, number
from .financial_amms import money, monthly_rows
from .financial_spark import saved_component
from .statement_rollups import combine
from .verification import digest

SNAPSHOT = "7dc9b71c3de9783499b5160738fa5c63f7dfbd8ee115d16711d9d5fcd8184013"
PSM = "investment.psm3.susds"
ASSET_FX = "revaluation.sparklend_assets"
LIABILITY_FX = "revaluation.savings_liabilities"


@lru_cache(maxsize=1)
def evidence():
    raw = (
        files("settle.revenue")
        .joinpath("published_reconciliation", SNAPSHOT + ".json")
        .read_text()
        .strip()
    )
    if hashlib.sha256(raw.encode()).hexdigest() != SNAPSHOT:
        raise ValueError("Reviewed Spark evidence changed")
    result = json.loads(raw)
    if result["schema_version"] != "spark-anchorage-reviewed-cash.v1":
        raise ValueError("Unsupported reviewed Spark evidence")
    for row in result["months"]:
        delta = sum(
            (
                number(e["amount"])
                * ((e["to"] == result["escrow"]) - (e["from"] == result["escrow"]))
                for e in row["events"]
            ),
            Decimal(0),
        )
        if number(row["opening_escrow_USDC"]) + delta != number(row["closing_escrow_USDC"]):
            raise ValueError("Reviewed escrow balances do not reconcile")
        if (
            row["chain"] != "ethereum"
            or row["token"] != "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
            or row["decimals"] != 6
        ):
            raise ValueError("Reviewed escrow asset scope differs")
        for event in row["events"]:
            if (
                not row["opening_block"] < event["block"] <= row["closing_block"]
                or not event["date"].startswith(row["month"])
                or number(event["amount"]) < 0
                or result["escrow"] not in (event["from"], event["to"])
            ):
                raise ValueError("Reviewed escrow event scope differs")
        if len({(e["block"], e["log_index"]) for e in row["events"]}) != len(row["events"]):
            raise ValueError("Duplicate reviewed escrow event")
    return result


def blank(period, reason):
    return {
        **metric(reason=reason),
        **({"status": "future"} if period["state"] == "future" else {}),
    }


def row(key, label, cells, **extra):
    return {
        "id": key,
        "label": label,
        "aggregation": "leaf",
        "values": cells,
        "total": metric(reason="computed_by_rollups"),
        **extra,
    }


def published_credit(report):
    if not report:
        return None
    matches = [
        line
        for line in report["raw_summary"].splitlines()
        if line.startswith("| PSM3 sUSDS SSR appreciation |")
    ]
    if len(matches) > 1:
        raise ValueError("Ambiguous PSM3 SSR credit")
    if not matches:
        return None
    value = number(matches[0].split("|")[2].strip().replace(",", ""))
    if value < 0:
        raise ValueError("Negative published SSR credit")
    return {
        **metric(
            value,
            basis="published_nonvenue_PSM3_sUSDS_SSR_appreciation",
            source_paths=[report["source"]["url"] + "#non-venue-susds-credits"],
        ),
        "source_ref": report["revision_id"],
        "quality": "supported",
        "period_start": report["month"] + "-01",
        "period_end": report["cutoff"],
    }


def extend_inputs(statement, financial, monthly, supplements):
    lookup = {r["id"]: r for r in statement["lines"]}
    psm, assets, liabilities = {}, {}, {}
    audited = evidence()
    statement["spark_reviewed_cash_evidence"] = []
    for p, raw, published, saved in zip(
        statement["periods"], financial, monthly, supplements, strict=True
    ):
        month = p["key"]
        psm[month] = blank(p, "PSM3_SSR_source_not_published")
        assets[month] = blank(p, "asset_revaluation_breakdown_not_published")
        liabilities[month] = blank(p, "liability_revaluation_breakdown_not_published")
        if p["state"] == "future":
            continue
        credit = published_credit(published)
        if credit:
            psm[month] = credit
        elif (
            raw
            and raw.get("spark_psm3_income") is not None
            and raw.get("spark_psm3_source_hash") == raw["document"]["source"]["result_hash"]
        ):
            value = number(raw["spark_psm3_income"])
            if value < 0:
                raise ValueError("Invalid retained PSM3 SSR income")
            psm[month] = {
                **metric(
                    value,
                    basis="retained_PSM3_share_SSR_appreciation_excluding_capital",
                    source_paths=["result.psm3_susds_appreciation"],
                ),
                "source_ref": raw["revision_id"],
                "quality": "supported",
                "period_start": month + "-01",
                "period_end": raw["cutoff"],
            }
            if raw["cutoff"] < p["expected_cutoff"]:
                psm[month].update(
                    value=None,
                    known_value=str(value),
                    status="partial",
                    reason="PSM3_source_stops_before_selected_cutoff",
                )
        for source_id in (
            "investment.susds.ethereum",
            "investment.susds.base",
            "investment.susds.arbitrum",
            "investment.susds.optimism",
            "investment.susds.unichain",
        ):
            source_cell = lookup[source_id]["values"][month]
            if (
                published
                and source_cell.get("source_ref") == published["revision_id"]
                and source_cell.get("value") is not None
            ):
                source_cell.update(
                    known_value=source_cell["value"],
                    value=None,
                    status="partial",
                    quality="estimate",
                    reason="MSC_fallback_does_not_establish_gross_Savings_funded_yield",
                )
        if saved:
            original = saved["document"].get("gains", {}).get("sparklend_nonstable", {})
            net = lookup["gains.sparklend_nonstable"]["values"][month]
            if (
                original.get("asset_price_gain_usd") is not None
                and original.get("depositor_offset_usd") is not None
                and net.get("source_ref") == saved["revision_id"]
            ):
                asset, owed = (
                    number(original["asset_price_gain_usd"]),
                    number(original["depositor_offset_usd"]),
                )
                with localcontext() as ctx:
                    ctx.prec = 120
                    if asset - owed != number(original["value"]):
                        raise ValueError("Gross asset and depositor FX do not reproduce net gains")
                assets[month] = saved_component(
                    {
                        **original,
                        "value": str(asset),
                        "basis": "gross_SparkLend_nonstable_asset_revaluation",
                    },
                    "value",
                    saved,
                    expected=p["expected_cutoff"],
                )
                liabilities[month] = saved_component(
                    {
                        **original,
                        "value": str(owed.copy_negate()),
                        "basis": "Savings_ETH_liability_revaluation_signed_offset_not_VSR_interest",
                    },
                    "value",
                    saved,
                    expected=p["expected_cutoff"],
                )
                net.update(
                    combine([(ASSET_FX, 1, assets[month]), (LIABILITY_FX, 1, liabilities[month])])
                )
        fact = next((r for r in audited["months"] if r["month"] == month), None)
        cash = next((r for r in audited["pyusd_cash"]["monthly"] if r["month"] == month), None)
        end = published["cutoff"] if published else None
        if end and (fact or cash):
            facts = {
                "month": month,
                "anchorage": fact,
                "pyusd": cash,
                "pyusd_events": [
                    e for e in audited["pyusd_cash"]["events"] if e["date"].startswith(month)
                ],
            }
            ref = digest(facts)
            p["reconciliation_revision"] = ref
            statement["source_manifest"][ref] = {
                "kind": "spark_reviewed_chain_cash",
                "revision_id": ref,
                "result_hash": ref,
                "cutoff": end,
                "month": month,
                "currency": "USD",
                "snapshot_revision": SNAPSHOT,
            }
            statement["spark_reviewed_cash_evidence"].append({**facts, "source_ref": ref})
            if fact:
                daily = defaultdict(Decimal)
                for event in fact["events"]:
                    if event["from"] == audited["escrow"] and event["to"] == audited["alm"]:
                        daily[event["date"][:10]] += number(event["amount"])
                    elif event["from"] == audited["alm"] and event["to"] == audited["escrow"]:
                        daily[event["date"][:10]] -= number(event["amount"])
                received = sum((v for v in daily.values() if v > 0), Decimal(0))
                lookup["investment.anchorage"]["values"][month] = {
                    **metric(
                        received,
                        basis="reviewed_USDC_escrow_to_ALM_positive_day_net_receipts_no_principal_return_in_scope",
                        source_paths=[
                            "https://etherscan.io/tx/" + e["tx"]
                            for e in fact["events"]
                            if number(e["amount"]) > 1
                        ],
                    ),
                    "recognition": "receipt",
                    "source_ref": ref,
                    "quality": "supported",
                    "period_start": month + "-01",
                    "period_end": end,
                }
    statement["lines"].extend(
        [
            row(PSM, "PSM3 sUSDS yield", psm, family="sUSDS"),
            row(ASSET_FX, "SparkLend asset revaluation", assets),
            row(LIABILITY_FX, "Savings ETH liability revaluation", liabilities),
        ]
    )
    net = lookup["gains.sparklend_nonstable"]
    # Only the net parent enters statement.gains; child IDs deliberately do not
    # use the gains.* namespace, preventing asset/liability double counting.
    net.update(
        aggregation="subtotal",
        components=[{"id": ASSET_FX, "sign": 1}, {"id": LIABILITY_FX, "sign": 1}],
    )
    for period in statement["periods"]:
        key = period["key"]
        net["values"][key] = (
            blank(period, "FX_breakdown_not_published")
            if period["state"] == "future"
            else combine([(ASSET_FX, 1, assets[key]), (LIABILITY_FX, 1, liabilities[key])])
        )
    parent = lookup["investment.susds"]
    parent["components"] = [*parent["components"], {"id": PSM, "sign": 1}]
    for p in statement["periods"]:
        month = p["key"]
        if p["state"] != "future":
            parent["values"][month] = combine(
                [
                    (
                        c["id"],
                        c["sign"],
                        psm[month] if c["id"] == PSM else lookup[c["id"]]["values"][month],
                    )
                    for c in parent["components"]
                ]
            )


# Exact grouping is an attribution aid, not proof that every difference is valid.
VENUE_GROUPS = dict(
    zip(
        (
            "S23",
            "S26",
            "S32",
            "S37",
            "S43",
            "S47",
            "S51",
            "S28",
            "S64",
            "S69",
            "S61",
            "S62",
            "S66",
            "S24",
            "S25",
        ),
        (
            "anchorage_cash",
            "anchorage_cash",
            "susds_ethereum",
            "susds_base",
            "susds_arbitrum",
            "susds_optimism",
            "susds_unichain",
            "pyusd",
            "rlusd",
            "usdg",
            "uniswap_pyusd",
            "uniswap_usdt",
            "uniswap_rlusd",
            "curve_susds",
            "curve_pyusd",
        ),
        strict=True,
    )
)
FIXED = dict(
    zip(
        (
            "investment.anchorage",
            "investment.sparklend.weth",
            "investment.morpho.sentora_rlusd",
            "investment.susds.ethereum",
            "investment.susds.base",
            "investment.susds.arbitrum",
            "investment.susds.optimism",
            "investment.susds.unichain",
            "rewards.issuer.pyusd",
            "rewards.issuer.rlusd",
            "rewards.issuer.usdg",
            PSM,
        ),
        (
            "anchorage_cash",
            "weth_supply",
            "sentora_rlusd",
            "susds_ethereum",
            "susds_base",
            "susds_arbitrum",
            "susds_optimism",
            "susds_unichain",
            "pyusd",
            "rlusd",
            "usdg",
            "psm3",
        ),
        strict=True,
    )
)
AAVE = dict(
    zip(
        (
            "eth_lido_usds",
            "eth_usds",
            "eth_usdc",
            "eth_usdt",
            "base_usdc",
            "arbitrum_usdc",
            "avalanche_usdc",
        ),
        ("S6", "S7", "S8", "S9", "S35", "S41", "S54"),
        strict=True,
    )
)
AMMS = {
    "investment.amm.uniswap_v4." + suffix: VENUE_GROUPS[v]
    for suffix, v in zip(
        ("pyusd_usds", "usdt_usds", "rlusd_usds"), ("S61", "S62", "S66"), strict=True
    )
}
AMMS.update(
    {
        "investment.amm.curve.susds_usdt": "curve_susds",
        "investment.amm.curve.pyusd_usds": "curve_pyusd",
    }
)
RWA = dict(zip(("buidl", "jtrsy", "ustb", "uscc"), ("S19", "S20", "S21", "S22"), strict=True))


def group(key):
    if key in FIXED:
        return FIXED[key]
    if key in AMMS:
        return AMMS[key]
    if key.startswith("investment.venue."):
        return key.rsplit(".", 1)[1]
    if key.startswith("rewards.lending.aave."):
        return AAVE[key.rsplit(".", 1)[1]]
    if key.startswith("investment.rwa."):
        return RWA[key.rsplit(".", 1)[1]]
    return key


def headline(report, label, section):
    if not report:
        return metric(reason="MSC_summary_not_published")
    from .monthly_reports import table

    text = report["raw_summary"].split(section, 1)[1].split("\n##", 1)[0]
    parsed = table(text)
    if label not in parsed:
        raise ValueError("Missing MSC comparison headline")
    return {
        **metric(
            parsed[label], basis="published_MSC_headline", source_paths=[report["source"]["url"]]
        ),
        "source_ref": report["revision_id"],
        "quality": "supported",
    }


def attach_bridge(statement, monthly):
    lookup = {r["id"]: r for r in statement["lines"]}
    bridges = []
    for p, report in zip(statement["periods"], monthly, strict=True):
        month = p["key"]
        if p["state"] == "future":
            continue
        savings = deepcopy(lookup["funding.savings"]["values"][month])
        nii = deepcopy(lookup["statement.net_interest"]["values"][month])
        net = deepcopy(lookup["statement.net_revenue"]["values"][month])
        supply = headline(report, "supply-side revenue", "#### Supply-Side revenue\n")
        demand = headline(report, "demand-side revenue", "#### Demand-Side revenue\n")
        groups = []
        rounding = metric(reason="MSC_summary_not_published")
        funding_delta = metric(reason="MSC_summary_not_published")
        if report:
            expected = defaultdict(Decimal)
            labels = {}
            for r in monthly_rows(report):
                key = VENUE_GROUPS.get(r["Venue"], r["Venue"])
                expected[key] += money(r["revenue"])
                labels[key] = r["Label"]
            credit = published_credit(report)
            if credit:
                expected["psm3"] = number(credit["value"])
                labels["psm3"] = "PSM3 sUSDS SSR appreciation"
            observed = defaultdict(list)
            for c in lookup["statement.interest"]["components"]:
                key = c["id"]
                observed[group(key)].append((key, c["sign"], lookup[key]["values"][month]))
            for key in sorted(expected.keys() | observed.keys()):
                if not observed[key] and expected.get(key, Decimal(0)) == 0:
                    continue
                actual = (
                    combine(observed[key])
                    if observed[key]
                    else metric(reason="MSC_venue_has_no_reporting_mapping")
                )
                outside = key in ("weth_supply", "sentora_rlusd", "rlusd", "usdg", "S70", "S71")
                msc = (
                    {
                        **metric(
                            expected.get(key, Decimal(0)),
                            basis="published_MSC_per_venue_revenue",
                            source_paths=[report["source"]["url"] + "#per-venue"],
                        ),
                        "source_ref": report["revision_id"],
                    }
                    if key in expected or outside
                    else metric(reason="reporting_venue_not_in_selected_MSC_summary")
                )
                delta = combine([("reporting", 1, actual), ("MSC", -1, msc)])
                value = delta["value"] if delta["value"] is not None else delta.get("known_value")
                if value is not None and number(value) == 0:
                    continue
                known = key in (
                    "psm3",
                    "pyusd",
                    "rlusd",
                    "usdg",
                    "weth_supply",
                    "sentora_rlusd",
                ) or key.startswith("susds_")
                reason = (
                    "accrual_vs_MSC_cash_receipts"
                    if key == "pyusd"
                    else "gross_yield_including_Savings_funded_holdings"
                    if key.startswith("susds_")
                    else "separate_PSM3_share_yield"
                    if key == "psm3"
                    else "reporting_only_income_stream"
                    if key in ("rlusd", "usdg", "weth_supply", "sentora_rlusd")
                    else "escrow_capital_and_receipt_attribution_requires_review"
                    if key == "anchorage_cash"
                    else "venue_yield_fee_or_recognition_difference_requires_review"
                )
                groups.append(
                    {
                        "id": key,
                        "label": labels.get(
                            key, lookup[observed[key][0][0]]["label"] if observed[key] else key
                        ),
                        "MSC": msc,
                        "reporting": actual,
                        "difference": delta,
                        "reason": reason,
                        "review_status": "documented_method_difference"
                        if known
                        else "review_required",
                    }
                )
            msc_funding = headline(report, "prime cost of funds", "### Sky side\n")
            funding_delta = combine(
                [
                    ("MSC_funding", 1, msc_funding),
                    ("reporting_funding", -1, lookup["funding.sky.net"]["values"][month]),
                ]
            )
            per_venue = metric(
                sum(expected.values(), Decimal(0)),
                basis="sum_of_published_MSC_venue_revenue_and_PSM3_credit",
                source_paths=[report["source"]["url"]],
            )
            rounding = combine(
                [
                    ("sum_published_rows", 1, per_venue),
                    ("MSC_supply", -1, supply),
                    ("MSC_funding", -1, msc_funding),
                ]
            )
            if rounding["value"] is not None and abs(number(rounding["value"])) > Decimal("0.50"):
                raise ValueError("MSC per-venue/headline bridge exceeds display rounding")
        adjusted = combine([("MSC_supply", 1, supply), ("Savings_interest", -1, savings)])
        gap = combine([("reporting_NII", 1, nii), ("MSC_less_Savings", -1, adjusted)])
        differences = (
            combine([(x["id"], 1, x["difference"]) for x in groups])
            if report
            else metric(reason="MSC_summary_not_published")
        )
        bridged_nii = combine(
            [
                ("MSC_less_Savings", 1, adjusted),
                ("venue_differences", 1, differences),
                ("funding_difference", 1, funding_delta),
                ("published_rounding", 1, rounding),
            ]
        )
        residual = combine([("reporting_NII", 1, nii), ("bridged_NII", -1, bridged_nii)])
        other = [
            deepcopy(lookup[k]["values"][month])
            for k in ("fees.sparklend", "fees.curator", "statement.rewards", "statement.gains")
        ]
        total_base = combine(
            [
                ("MSC_supply", 1, supply),
                ("MSC_demand", 1, demand),
                ("Savings_interest", -1, savings),
            ]
        )
        total_gap = combine(
            [("reporting_net_revenue", 1, net), ("MSC_total_less_Savings", -1, total_base)]
        )
        if not report:
            adjusted = gap = total_base = total_gap = bridged_nii = residual = metric(
                reason="MSC_summary_not_published"
            )
        fact = next(
            (f for f in statement["spark_reviewed_cash_evidence"] if f["month"] == month), None
        )
        bridges.append(
            {
                "month": month,
                "status": "review_required" if report else "unavailable",
                "reason": "Venue differences are quantified, not automatically certified; incomplete sources and capital/fee classification still require review."
                if report
                else "MSC_summary_not_published",
                "source_ref": report["revision_id"] if report else None,
                "MSC_supply_side": supply,
                "MSC_demand_side": demand,
                "Savings_interest": savings,
                "MSC_supply_less_Savings": adjusted,
                "reporting_net_interest": nii,
                "net_interest_difference": gap,
                "allocation_difference_total": differences,
                "Sky_funding_difference": funding_delta,
                "published_summary_rounding": rounding,
                "bridged_net_interest": bridged_nii,
                "quantitative_bridge_residual": residual,
                "MSC_total_less_Savings": total_base,
                "reporting_net_revenue": net,
                "net_revenue_difference": total_gap,
                "other_reporting_components": dict(
                    zip(
                        ("sparklend_fees", "curator_fees", "other_rewards", "nonstable_gains"),
                        other,
                        strict=True,
                    )
                ),
                "venue_differences": groups,
                "reviewed_cash_evidence": fact,
                "principal_included": False,
                "operating_expenses_included": False,
            }
        )
    statement["spark_msc_reconciliation"] = bridges
    active_control = statement["as_of"] >= "2026-09-30"
    fee_cells = [
        lookup["fees.sparklend"]["values"].get(f"2026-{m:02}", metric()) for m in range(1, 9)
    ]
    fees = combine([(f"2026-{m:02}", 1, c) for m, c in enumerate(fee_cells, 1)])
    expected = metric(
        evidence()["reserve_fee_true_up"]["amount"],
        basis="documented_September_MSC_prior_period_true_up",
    )
    statement["spark_fee_true_up_control"] = {
        **evidence()["reserve_fee_true_up"],
        "reporting_original_months": fees,
        "difference": combine([("reporting_receipts", 1, fees), ("MSC_true_up", -1, expected)]),
        "added_to_reporting_income": False,
    }
    if not active_control:
        statement["spark_fee_true_up_control"].update(
            amount=None,
            reporting_original_months=metric(reason="fee_true_up_not_applicable_before_September"),
            difference=metric(reason="fee_true_up_not_applicable_before_September"),
        )
    # The API owns all additional income-statement rows and their indentation.
    additions = {
        "gains.sparklend_nonstable": [
            (ASSET_FX, "SparkLend asset revaluation"),
            (LIABILITY_FX, "Savings ETH liability revaluation"),
        ],
        "investment.susds": [(PSM, "PSM3 sUSDS yield")],
    }
    rows = []
    for r in statement["statement_rows"]:
        rows.append(r)
        for key, label in additions.get(r["id"], []):
            rows.append(
                {
                    "id": key,
                    "label": label,
                    "parent": r["id"],
                    "depth": r["depth"] + 1,
                    "kind": None,
                    "metric_id": key,
                }
            )
    statement["statement_rows"] = rows
    statement["limitations"].extend(
        [
            "Savings interest and depositor FX revaluation are distinct and each counted once.",
            "Published MSC reports are unchanged. Anchorage April third-party payment purposes remain unconfirmed.",
            "MSC true-ups are reconciliation controls, never additional reporting income.",
        ]
    )
