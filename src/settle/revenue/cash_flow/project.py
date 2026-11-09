"""Pure annual cash-flow projection; saved selections preserve absent months."""

import base64
import calendar
import json
from copy import deepcopy
from datetime import date
from decimal import Decimal

from ..verification import digest
from .config import layout, load, LEGACY_POLICY, REVIEW_POLICY
from .model import CHILDREN, OPTIONAL_ACCOUNTING_LINES, amount, metric, total


def encode(value):
    return (
        base64.urlsafe_b64encode(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def decode(value, prime, year):
    try:
        result = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if (
            set(result) != {"prime", "year", "as_of", "policy_version", "revisions"}
            or result["prime"] != prime
            or result["year"] != year
            or result["policy_version"] not in (LEGACY_POLICY, REVIEW_POLICY)
            or not isinstance(result["revisions"], list)
            or len(result["revisions"]) != 12
            or not all(
                v is None
                or (isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v))
                for v in result["revisions"]
            )
        ):
            raise ValueError
        if date.fromisoformat(result["as_of"]).year != year:
            raise ValueError
        return result
    except Exception:
        raise ValueError("Invalid cash-flow selection") from None


def annual(prime, year, as_of, rows, *, selection=None, review=True):
    if prime != "spark":
        raise ValueError("Cash-flow scope not configured")
    policy = selection["policy_version"] if selection else load(review=review)["policy"]
    review = policy == REVIEW_POLICY
    config = load(policy=policy)
    periods = []
    chosen = []
    manifest = {}
    for m in range(1, 13):
        first = date(year, m, 1)
        last = date(year, m, calendar.monthrange(year, m)[1])
        future = first > as_of
        expected = min(last, as_of) if not future else None
        if selection:
            ref = selection["revisions"][m - 1]
            item = next((r for r in rows if r["revision_id"] == ref and r["policy_version"] == policy), None) if ref else None
            if ref and not item:
                raise LookupError("Retained cash-flow month is unavailable")
        else:
            eligible = [
                r
                for r in rows
                if r["cutoff"][:7] == f"{year}-{m:02}" and r["policy_version"] == policy
                and not future
                and r["cutoff"] <= str(expected)
                and (first <= as_of < last or r["cutoff"] == str(expected))
            ]
            item = max(eligible, key=lambda r: (r["cutoff"], r["publication_order"]), default=None)
        if item and (
            future
            or item["cutoff"][:7] != f"{year}-{m:02}"
            or item["cutoff"] > str(expected)
            or (last <= as_of and item["cutoff"] != str(last))
        ):
            raise ValueError("Cash-flow month cutoff differs")
        chosen.append(item)
        periods.append(
            {
                "key": f"{year}-{m:02}",
                "state": "future" if future else "partial" if as_of < last else "complete",
                "expected_cutoff": str(expected) if expected else None,
                "actual_cutoff": item["cutoff"] if item else None,
                "revision": item["revision_id"] if item else None,
                "stale": bool(item and item["cutoff"] < str(expected)),
            }
        )
        if item:
            manifest[item["revision_id"]] = {
                k: item[k]
                for k in [
                    "cutoff",
                    "computed_at",
                    "document_hash",
                    "evidence_hash",
                    "code_version",
                    "configuration_revision",
                ]
            }
    definitions = [
        *layout(review=review),
        {
            "id": "cash-in-transit",
            "label": "Cash in transit",
            "parent": None,
            "depth": 0,
            "kind": None,
            "check": True,
            "aggregation": "last",
        },
        {
            "id": "unclassified-net",
            "label": "Unclassified cash movements, net",
            "parent": None,
            "depth": 0,
            "kind": None,
            "check": True,
            "aggregation": "sum",
        },
        {
            "id": "gross-allocation-cash",
            "label": "Allocation cash before principal/yield split",
            "parent": None,
            "depth": 0,
            "kind": None,
            "check": True,
            "aggregation": "sum",
        },
        {
            "id": "msc-receivable",
            "label": "MSC receivable",
            "parent": None,
            "depth": 0,
            "kind": None,
            "check": True,
            "aggregation": "last",
        },
    ]
    present = {key for item in chosen if item for key in item["document"]["metrics"]}
    definitions = [r for r in definitions if r["id"] not in OPTIONAL_ACCOUNTING_LINES or r["id"] in present]
    lines = []
    for row in definitions:
        values = {}
        for p, item in zip(periods, chosen, strict=True):
            cell = (
                deepcopy(
                    item["document"]["metrics"].get(row["id"], metric(reason="Not an amount line"))
                )
                if item
                else metric(reason="Cash-flow month has not been published")
            )
            if p["state"] == "future":
                cell = {**metric(reason="future_period"), "status": "future"}
            if item and p["stale"] and amount(cell) is not None:
                cell = {
                    **cell,
                    "known_value": str(amount(cell)),
                    "value": None,
                    "status": "partial",
                    "reason": "Delayed cash-flow publication: observed through " + item["cutoff"],
                }
            if item:
                cell["source_ref"] = item["revision_id"]
            values[p["key"]] = cell
        elapsed = [p for p in periods if p["state"] != "future"]
        if row["aggregation"] == "first":
            selected = elapsed[:1]
        elif row["aggregation"] == "last":
            selected = elapsed[-1:]
        else:
            selected = elapsed
        combined = total([(p["key"], 1, values[p["key"]]) for p in selected])
        lines.append({**row, "values": values, "total": combined})
    by_id = {r["id"]: r for r in lines}
    # Additive lines can coexist with retained legacy months. Unknown new
    # components make those months partial; they must not break parent sums.
    if present.intersection(OPTIONAL_ACCOUNTING_LINES):
        for key, children in CHILDREN.items():
            for p, item in zip(periods, chosen, strict=True):
                if p["state"] == "future": continue
                cells = [(child, 1, by_id[child]["values"][p["key"]]) for child in children if child in by_id]
                cell = total(cells)
                if item: cell["source_ref"] = item["revision_id"]
                by_id[key]["values"][p["key"]] = cell
            by_id[key]["total"] = total([(p["key"],1,by_id[key]["values"][p["key"]]) for p in periods if p["state"] != "future"])
    for key, starting, parts in [
        ("cumulative-gap", Decimal(0), [("reconciliation-gap", 1)]),
        (
            "msc-receivable",
            Decimal(config["opening_msc_receivable"]),
            [
                ("earned-not-received", -1),
                ("sky-allocation-receipts", -1),
                ("sky-demand-receipts", -1),
            ],
        ),
    ]:
        running = metric(
            starting,
            recognition="accrual" if key == "msc-receivable" else "unknown",
            refs=["approved_opening_msc_receivable"] if starting else [],
        )
        for p in periods:
            if p["state"] == "future":
                by_id[key]["values"][p["key"]] = {
                    **metric(reason="future_period"),
                    "status": "future",
                }
                continue
            terms = [(part, sign, by_id[part]["values"][p["key"]]) for part, sign in parts]
            bridge = by_id["earned-not-received"]["values"][p["key"]]
            if key == "msc-receivable" and any(
                name in bridge for name in ("sky_staking_income_component", "steakhouse_income_component")
            ):
                # The operating cash bridge reverses ALL noncash income, but
                # only approved MSC earnings can increase an amount owed by Sky.
                approved = bridge.get("approved_msc_component") or metric(
                    reason="Approved MSC entitlement is missing from the income-to-cash bridge")
                terms[0] = ("approved-msc-entitlement", 1, approved)
            running = total([("opening", 1, running), *terms])
            by_id[key]["values"][p["key"]] = deepcopy(running)
        by_id[key]["total"] = deepcopy(running)
    selected = {
        "prime": prime,
        "year": year,
        "as_of": str(as_of),
        "policy_version": policy,
        "revisions": [r["revision_id"] if r else None for r in chosen],
    }
    result = {
        "schema_version": "cash-flow.v1",
        "policy_version": selected["policy_version"],
        "prime": prime,
        "year": year,
        "as_of": str(as_of),
        "selection": encode(selected),
        "periods": periods,
        "lines": lines,
        "source_manifest": manifest,
        "monthly_coverage": {
            p["key"]: r["document"]["coverage"] if r else None
            for p, r in zip(periods, chosen, strict=True)
        },
        "methodology": {
            "opening_msc_receivable": config["opening_msc_receivable"],
            "opening_msc_source": "https://forum.skyeco.com/t/msc-4-settlement-summary-november-and-december-2025-spark-grove/27617",
            "cash_basis": "actual_transaction_dates",
            "valuation": "USD par for configured stablecoins",
            "principal_interest_policy": "Savings principal financing; interest paid operating; weighted-average deposited principal follows transferred shares",
            "unknowns": "Unidentified events remain separate; no balancing plugs",
            "scope": "Workbook operational Spark cluster; missing chains/bridge and reward balances are explicit",
        },
        "source_note": "Cash amounts use event evidence. Net income/expenses use retained reporting inputs; provisional or unclassified components retain their status.",
    }
    result["report_hash"] = digest(result)
    return result
