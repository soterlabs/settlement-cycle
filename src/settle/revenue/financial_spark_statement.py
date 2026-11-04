"""Complete declared Spark revenue perimeter and API-owned statement layout.

Project immutable inputs only. Expense principal and depositor cash flows are
never income. DR remains unavailable until its source summary is published.
"""

import json
from copy import deepcopy
from decimal import localcontext
from importlib.resources import files

from .financial import metric, number
from .financial_spark import saved_component
from .spark_completion import SUSDS
from .spark_savings import VAULTS
from .statement_rollups import combine

SAVINGS = {
    "usdc": ("S56", "S60"),
    "usdt": ("S57",),
    "eth": ("S58",),
    "pyusd": ("S59",),
    "usdg": ("S63",),
}
SUSDS_KEYS = dict(zip(SUSDS, ("ethereum", "base", "arbitrum", "optimism", "unichain"), strict=True))
RWA = {
    "S19": ("buidl", "BUIDL"),
    "S20": ("jtrsy", "JTRSY"),
    "S21": ("ustb", "USTB"),
    "S22": ("uscc", "USCC"),
}


def taxonomy(prime):
    if prime != "spark":
        return []
    return [
        {
            "id": "investment.sparklend.weth",
            "label": "WETH supply revenue",
            "family": "SparkLend",
            "aggregation": "leaf",
        },
        {
            "id": "investment.morpho.sentora_rlusd",
            "label": "Sentora RLUSD",
            "family": "Morpho vaults",
            "aggregation": "leaf",
        },
        {
            "id": "investment.anchorage",
            "label": "Anchorage interest receipts",
            "family": "Anchorage",
            "aggregation": "leaf",
        },
        {"id": "investment.susds", "label": "sUSDS", "aggregation": "subtotal"},
        *(
            {
                "id": "investment.susds." + chain,
                "label": "sUSDS (" + chain + ")",
                "family": "sUSDS",
                "aggregation": "leaf",
            }
            for chain in SUSDS_KEYS.values()
        ),
        *(
            {
                "id": "investment.rwa." + key,
                "label": label,
                "family": "RWA vaults",
                "aggregation": "leaf",
            }
            for key, label in RWA.values()
        ),
        {"id": "funding.savings", "label": "Savings vault interest", "aggregation": "subtotal"},
        *(
            {
                "id": "funding.savings." + key,
                "label": "Savings " + key.upper(),
                "aggregation": "subtotal",
            }
            for key in SAVINGS
        ),
        *(
            {
                "id": "funding.savings.venue." + venue,
                "label": venue + " depositor interest",
                "aggregation": "leaf",
            }
            for venue in VAULTS
        ),
    ]


def extend(statement, chosen, supplemental):
    lookup = {r["id"]: r for r in statement["lines"]}
    statement["spark_savings_ledger"] = []

    def subtotal(key, ids):
        row = lookup[key]
        row["components"] = [{"id": i, "sign": 1} for i in ids]
        for period in statement["periods"]:
            month = period["key"]
            if period["state"] == "future":
                row["values"][month] = {**metric(reason="future_period"), "status": "future"}
            else:
                row["values"][month] = {
                    **combine([(i, 1, lookup[i]["values"][month]) for i in ids]),
                    "component_ids": ids,
                }

    for period, raw, saved in zip(statement["periods"], chosen, supplemental, strict=True):
        month = period["key"]
        if period["state"] == "future":
            continue
        if saved:
            doc = saved["document"]
            for venue in VAULTS:
                source = doc.get("savings_interest", {}).get(venue, {})
                key = "funding.savings.venue." + venue
                lookup[key]["values"][month] = saved_component(
                    source, "value", saved, expected=period["expected_cutoff"]
                )
                if source.get("status") in ("available", "partial"):
                    statement["spark_savings_ledger"].append(
                        {**source, "month": month, "source_ref": saved["revision_id"]}
                    )
            for source_id, key in [
                ("sparklend.weth", "investment.sparklend.weth"),
                ("morpho.sentora_rlusd", "investment.morpho.sentora_rlusd"),
                *((venue, "investment.susds." + chain) for venue, chain in SUSDS_KEYS.items()),
            ]:
                lookup[key]["values"][month] = saved_component(
                    doc.get("statement_allocations", {}).get(source_id, {}),
                    "value",
                    saved,
                    expected=period["expected_cutoff"],
                )
            # Replace the partial legacy V4 price return with price + earned
            # trading fees, applying the same saved Sky share once.
            for venue, key in (("S61", "pyusd_usds"), ("S62", "usdt_usds"), ("S66", "rlusd_usds")):
                item = lookup["investment.amm.uniswap_v4." + key]["values"][month]
                fee = doc.get("amm_fees", {}).get(venue, {})
                if fee.get("status") != "available" or item.get("allocation_split") is None:
                    continue
                if saved["cutoff"] != item.get("period_end"):
                    continue
                if (
                    not raw
                    or raw.get("spark_statement_opening_pins") != doc.get("opening_pins")
                    or raw.get("spark_statement_closing_pins") != doc.get("closing_pins")
                ):
                    continue
                split = item["allocation_split"]
                with localcontext() as ctx:
                    ctx.prec = 60
                    fees = number(fee["value"])
                    share = number(split["sky_share"])
                    gross = number(split["gross_income_usd"]) + fees
                    sky = number(split["sky_income_usd"]) + fees * share
                    native = gross - sky
                item.update(
                    value=str(native),
                    known_value=None,
                    status="available",
                    reason=None,
                    quality="supported",
                    basis="LP_value_return_plus_accrued_V4_trading_fees_less_saved_Sky_share",
                    source_refs=[item.pop("source_ref"), saved["revision_id"]],
                )
                item["source_paths"] = [
                    *item["source_paths"],
                    "reporting_spark_components.document.amm_fees." + venue,
                ]
                item["allocation_split"] = {
                    **split,
                    "gross_income_usd": str(gross),
                    "sky_income_usd": str(sky),
                    "prime_income_usd": str(native),
                }
                if item["period_end"] < period["expected_cutoff"]:
                    item.update(
                        value=None,
                        known_value=str(native),
                        status="partial",
                        reason="amm_source_does_not_cover_selected_period",
                    )
                for ledger in statement.get("spark_amm_ledger", []):
                    if ledger["month"] == month and ledger["venue_id"] == venue:
                        if number(ledger["net_capital_added_usd"]) != 0:
                            item["quality"] = "estimate"
                        ledger.update(
                            income=item,
                            fee_coverage="accrued_V4_fees_reconciled",
                            accrued_trading_fees=deepcopy(fee),
                            gross_position_return_usd=str(gross),
                            sky_return_usd=str(sky),
                            spark_position_return_usd=str(native),
                        )
        if raw and raw.get("spark_amm_source_hash") == raw["document"]["source"]["result_hash"]:
            views = raw.get("spark_statement_source_views") or []
            for venue in ("S23", *RWA):
                matches = [r for r in views if r.get("venue_id") == venue and r.get("enabled")]
                if len(matches) != 1:
                    continue
                row = matches[0]
                if venue == "S23":
                    attribution = row.get("receipt_attribution", {})
                    if (
                        attribution.get("source_venue") != "S26"
                        or attribution.get("payer", "").lower()
                        != "0x49506c3aa028693458d6ee816b2ec28522946872"
                    ):
                        continue
                    value = row.get("cash_received_usd")
                    key = "investment.anchorage"
                else:
                    if row.get("status") != "available":
                        continue
                    value = row.get("income_usd")
                    key = "investment.rwa." + RWA[venue][0]
                if value is None:
                    continue
                item = metric(
                    number(value),
                    basis="verified_Anchorage_interest_receipts_excluding_principal"
                    if venue == "S23"
                    else "retained_RWA_revenue_excluding_capital_flows",
                    source_paths=["result.venue_views.reporting." + venue],
                )
                item.update(
                    source_ref=raw["revision_id"],
                    period_start=month + "-01",
                    period_end=raw["cutoff"],
                    quality="supported",
                    recognition="receipt" if venue == "S23" else "investment_return",
                )
                if raw["cutoff"] < period["expected_cutoff"]:
                    item.update(
                        status="partial",
                        known_value=item["value"],
                        value=None,
                        reason="source_does_not_cover_selected_period",
                    )
                lookup[key]["values"][month] = item
    for asset, venues in SAVINGS.items():
        subtotal("funding.savings." + asset, ["funding.savings.venue." + v for v in venues])
    subtotal("funding.savings", ["funding.savings." + a for a in SAVINGS])
    subtotal("investment.susds", ["investment.susds." + chain for chain in SUSDS_KEYS.values()])


def attach_rows(statement):
    """Every numeric display row resolves an explicit API metric, even TBD."""
    rows = json.loads(files("settle.revenue").joinpath("spark_statement_layout.json").read_text())
    lookup = {r["id"]: r for r in statement["lines"]}
    extras = [
        {
            "id": "statement.family.rwa_vaults",
            "label": "RWA vaults",
            "parent": "interest",
            "depth": 2,
            "kind": None,
            "metric_id": "statement.family.rwa_vaults",
        },
        *(
            {
                "id": "investment.rwa." + key,
                "label": label,
                "parent": "statement.family.rwa_vaults",
                "depth": 3,
                "kind": None,
                "metric_id": "investment.rwa." + key,
            }
            for key, label in RWA.values()
        ),
    ]
    at = next(i for i, r in enumerate(rows) if r["id"] == "statement.family.maple")
    rows[at:at] = extras
    for row in rows:
        key = row["metric_id"]
        if key and key not in lookup:
            cells = {
                p["key"]: {
                    **metric(reason="not_in_revenue_perimeter"),
                    **({"status": "future"} if p["state"] == "future" else {}),
                }
                for p in statement["periods"]
            }
            item = {
                "id": key,
                "label": row["label"],
                "values": cells,
                "total": metric(reason="not_in_revenue_perimeter"),
            }
            statement["lines"].append(item)
            lookup[key] = item
    statement.update(
        statement_rows=rows,
        display_decimals=0,
        revenue_perimeter="configured_Spark_allocations_fees_rewards_and_net_nonstable_revaluation",
        limitations=[
            "Anchorage is aggregate cash-basis interest; individual loan accrual terms remain unconfirmed",
            "Savings ETH interest is exact in WETH and translated at daily closing USD prices",
        ],
    )
