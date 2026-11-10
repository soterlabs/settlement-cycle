"""Spark closing stocks from frozen reporting inputs; never accumulate principal flows."""

import base64
import calendar
import json
from copy import deepcopy
from datetime import date
from decimal import Decimal

from .cash_flow.config import REVIEW_POLICY
from .cash_flow.model import amount, metric, total
from .cash_flow.project import annual as cash_annual
from .verification import digest

POLICY = "spark-balance-sheet.closing-stocks.v1"
GROUPS = [
    ("sparklend", "SparkLend", ["S1", "S2", "S3", "S4", "S5"]),
    ("aave", "Aave v3", ["S6", "S7", "S8", "S9", "S35", "S41", "S54"]),
    ("morpho", "Morpho vaults", ["S10", "S11", "S65", "S12", "S13", "S34"]),
    ("maple", "Maple", ["S14", "S15"]),
    ("arkis", "Arkis", ["S18"]),
    ("rwa", "RWA vaults", ["S19", "S20", "S21", "S22"]),
    ("anchorage", "Anchorage", ["S23"]),
    ("amm", "AMM pools", ["S24", "S25", "S61", "S62", "S66"]),
    ("susds", "sUSDS", ["S32", "S37", "S43", "S47", "S51"]),
    ("issuer_tokens", "Yield-earning stablecoin balances", ["S28", "S64"]),
    ("other_vaults", "Other vaults", ["S16", "S17", "S36", "S42"]),
]
SAVINGS = [
    ("S56", "Savings USDC"),
    ("S57", "Savings USDT"),
    ("S58", "Savings ETH"),
    ("S59", "Savings PYUSD"),
    ("S60", "Savings USDC (Avalanche)"),
    ("S63", "Savings USDG"),
]
REWARDS = [("S67", "RLUSD"), ("S68", "PYUSD"), ("S69", "USDG")]


def encode(value):
    return (
        base64.urlsafe_b64encode(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def decode(value, prime, year):
    def require(condition):
        if not condition:
            raise ValueError("Invalid selection")

    try:
        d = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        require(set(d) == {"policy_version", "prime", "year", "as_of", "months"})
        require(d["policy_version"] == POLICY and d["prime"] == prime and d["year"] == year)
        require(
            date.fromisoformat(d["as_of"]).year == year
            and date.fromisoformat(d["as_of"]).isoformat() == d["as_of"]
            and isinstance(d["months"], list)
            and len(d["months"]) == 12
        )
        for i, item in enumerate(d["months"]):
            if item is None:
                continue
            require(set(item) == {"cutoff", "venues", "cash", "components"})
            require(date.fromisoformat(item["cutoff"]).strftime("%Y-%m") == f"{year}-{i + 1:02}")
            require(date.fromisoformat(item["cutoff"]).isoformat() == item["cutoff"])
            require(any(item[k] for k in ("venues", "cash", "components")))
            require(
                all(
                    v is None
                    or (
                        isinstance(v, str)
                        and len(v) == 64
                        and all(c in "0123456789abcdef" for c in v)
                    )
                    for k, v in item.items()
                    if k != "cutoff"
                )
            )
        return d
    except (ValueError, TypeError, KeyError):
        raise ValueError("Invalid balance sheet selection") from None


def stock(value=None, *, reason=None, ref=None, quality="supported"):
    result = metric(value, recognition="closing_balance", reason=reason, quality=quality)
    result.update(basis="saved_closing_stock", source_refs=[ref] if ref else [])
    return result


def combine(terms):
    result = total(
        [(key, sign, cell) for key, sign, cell in terms],
        reason="Some closing balances are not yet sourced",
    )
    result.update(
        basis="balance_sheet_components",
        recognition="closing_balance",
        source_refs=sorted({ref for _, _, c in terms for ref in c.get("source_refs", [])}),
    )
    return result


def annual(year, as_of, inputs, *, selection=None):
    if year != 2026 or as_of.year != year:
        raise ValueError("Balance sheet policy supports Spark 2026")
    rows, layout, manifest, periods, chosen = {}, [], {}, [], []

    def add(key, label, parent=None, kind=None, components=None):
        depth = next((r["depth"] + 1 for r in layout if r["id"] == parent), 0)
        layout.append(
            dict(
                id=key,
                label=label,
                parent=parent,
                depth=depth,
                kind=kind,
                components=components or [],
            )
        )
        rows[key] = {}

    add("assets", "Assets", kind="section")
    add("cash", "Cash and cash equivalents", "assets")
    add("allocations", "Allocation assets", "assets", "subtotal")
    for group, label, members in GROUPS:
        key = "allocations." + group
        add(key, label, "allocations", "subtotal")
        for venue in members:
            add("position." + venue, venue, key)
        if group == "morpho":
            add("position.steakhouse", "3F x Steakhouse USDC", key)
    add("allocation_gaps", "Other allocation assets", "allocations")
    add("nonstable", "Non-stable treasury assets", "assets")
    add("msc_receivable", "MSC receivable from Sky", "assets")
    add("reward_receivables", "Accrued issuer rewards", "assets", "subtotal")
    for venue, label in REWARDS:
        add("reward_receivable." + venue, label, "reward_receivables")
    add("other_receivables", "Other receivables", "assets")
    add("total_assets", "Total assets", kind="total")
    add("liabilities", "Liabilities", kind="section")
    add("sky_debt", "Debt to Sky", "liabilities")
    add("savings", "Savings depositor liabilities", "liabilities", "subtotal")
    for venue, label in SAVINGS:
        add("savings." + venue, label, "savings")
    add("savings.eliminations", "Controlled-holder share eliminations", "savings")
    add("msc_payable", "MSC balance due to Sky", "liabilities")
    add("reward_advances", "Prepaid issuer rewards", "liabilities", "subtotal")
    for venue, label in REWARDS:
        add("reward_advance." + venue, label, "reward_advances")
    add("payables", "Accrued expenses and other liabilities", "liabilities")
    add("total_liabilities", "Total liabilities", kind="total")
    add("equity", "Equity", kind="section")
    add("contributed_capital", "Contributed capital and reserves", "equity")
    add("opening_earnings", "Opening retained earnings", "equity")
    add("current_earnings", "Current-year net income", "equity")
    add("total_equity", "Total equity", kind="total")
    add("liabilities_equity", "Total liabilities and equity", kind="total")
    for row in layout:
        if row["kind"] == "subtotal":
            row["components"] = [
                {"id": r["id"], "sign": 1} for r in layout if r["parent"] == row["id"]
            ]
    for total_id, section in [
        ("total_assets", "assets"),
        ("total_liabilities", "liabilities"),
        ("total_equity", "equity"),
    ]:
        next(r for r in layout if r["id"] == total_id)["components"] = [
            {"id": r["id"], "sign": 1} for r in layout if r["parent"] == section
        ]
    next(r for r in layout if r["id"] == "liabilities_equity")["components"] = [
        {"id": k, "sign": 1} for k in ["total_liabilities", "total_equity"]
    ]
    for m in range(1, 13):
        month = f"{year}-{m:02}"
        first, last = date(year, m, 1), date(year, m, calendar.monthrange(year, m)[1])
        expected = min(last, as_of) if first <= as_of else None
        sources = {
            k: [
                r
                for r in inputs[k]
                if r["cutoff"][:7] == month
                and expected
                and r["cutoff"] <= str(expected)
                and (last > as_of or r["cutoff"] == str(last))
            ]
            for k in ["venues", "cash", "components"]
        }
        if selection:
            selected = selection["months"][m - 1]
        else:
            dates = [set(r["cutoff"] for r in source) for source in sources.values()]
            common = set.intersection(*dates)
            candidates = common or set.union(*dates)
            cutoff = max(candidates) if candidates else None
            selected = (
                {
                    "cutoff": cutoff,
                    **{
                        k: max(
                            (r for r in v if r["cutoff"] == cutoff),
                            key=lambda r: r["publication_order"],
                            default={},
                        ).get("revision_id")
                        for k, v in sources.items()
                    },
                }
                if cutoff
                else None
            )
        if selected and (
            not expected
            or selected["cutoff"] > str(expected)
            or (last <= as_of and selected["cutoff"] != str(last))
        ):
            raise ValueError("Balance sheet cutoff differs")
        selected_rows = {}
        for kind, records in sources.items():
            ref = selected[kind] if selected else None
            found = (
                next(
                    (
                        r
                        for r in records
                        if r["revision_id"] == ref and r["cutoff"] == selected["cutoff"]
                    ),
                    None,
                )
                if ref
                else None
            )
            if ref and not found:
                raise LookupError("Retained balance sheet source is unavailable")
            selected_rows[kind] = found
            if found:
                identity = kind + ":" + ref
                manifest[identity] = dict(
                    kind=kind,
                    revision=ref,
                    cutoff=found["cutoff"],
                    source_hash=found.get("document_hash", found.get("result_hash")),
                )
        chosen.append(selected)
        periods.append(
            dict(
                key=month,
                state="future" if not expected else "complete" if last <= as_of else "partial",
                expected_cutoff=str(expected) if expected else None,
                actual_cutoff=selected["cutoff"] if selected else None,
                stale=bool(expected and selected and selected["cutoff"] < str(expected)),
                sources={
                    k: k + ":" + v for k, v in (selected or {}).items() if k != "cutoff" and v
                },
            )
        )
        for key in rows:
            rows[key][month] = (
                stock(reason="Closing balance not yet sourced")
                if expected
                else {**stock(reason="future_period"), "status": "future"}
            )
        if not expected:
            continue
        raw = selected_rows["venues"]
        venue_map = {v["venue_id"]: v for v in (raw or {}).get("venues", [])}
        source_ref = "venues:" + raw["revision_id"] if raw else None

        def position(venue, venue_map=venue_map, source_ref=source_ref):
            v = venue_map.get(venue)
            if (
                not v
                or not v.get("enabled")
                or v.get("status") != "available"
                or v.get("position_value_usd") is None
            ):
                return stock(reason="No supported closing position for this venue")
            value = Decimal(v["position_value_usd"])
            if not value.is_finite():
                raise ValueError("Nonfinite closing position")
            if value < 0 and venue not in {v for v, _ in REWARDS}:
                return stock(reason="Negative closing asset requires separate classification")
            return stock(
                value,
                ref=source_ref,
                quality="estimate" if v.get("method") != "existing" else "supported",
            )

        for _, _, members in GROUPS:
            for venue in members:
                cell = position(venue)
                if amount(cell) and (
                    venue == "S62" or (venue == "S24" and selected["cutoff"] <= "2026-06-25")
                ):
                    cell = stock(
                        reason="Sky/prime closing ownership split is not yet independently sourced"
                    )
                rows["position." + venue][month] = cell
                if venue in venue_map:
                    next(r for r in layout if r["id"] == "position." + venue)["label"] = venue_map[
                        venue
                    ]["label"].split(" (")[0]
        for venue, _ in SAVINGS:
            rows["savings." + venue][month] = position(venue)
        for venue, _ in REWARDS:
            c = position(venue)
            for key, sign in [("reward_receivable.", 1), ("reward_advance.", -1)]:
                rows[key + venue][month] = (
                    {
                        **c,
                        "value": str(
                            max(Decimal(0), amount(c) if sign == 1 else amount(c).copy_negate())
                        ),
                    }
                    if c["status"] == "available"
                    else c
                )
        comp = selected_rows["components"]
        native = (comp or {}).get("document", {}).get("steakhouse", {})
        if native.get("status") == "available" and native.get("closing_value") is not None:
            rows["position.steakhouse"][month] = stock(
                native["closing_value"], ref="components:" + comp["revision_id"]
            )
    # Use exactly the selected cash publications, never a newer incompatible cutoff.
    cash_selection = dict(
        prime="spark",
        year=year,
        as_of=str(as_of),
        policy_version=REVIEW_POLICY,
        revisions=[s["cash"] if s else None for s in chosen],
    )
    cash = cash_annual("spark", year, as_of, inputs["cash"], selection=cash_selection)
    cash_lines = {r["id"]: r for r in cash["lines"]}
    earnings = []
    for period, selected in zip(periods, chosen, strict=True):
        month = period["key"]
        if period["state"] == "future":
            continue
        ref = "cash:" + selected["cash"] if selected and selected["cash"] else None
        for target, source in [("cash", "measured-cash")]:
            cell = deepcopy(cash_lines[source]["values"][month])
            cell.update(recognition="closing_balance", source_refs=[ref] if ref else [])
            rows[target][month] = cell
        c = cash_lines["msc-receivable"]["values"][month]
        for target, sign in [("msc_receivable", 1), ("msc_payable", -1)]:
            rows[target][month] = (
                stock(max(Decimal(0), amount(c) if sign == 1 else amount(c).copy_negate()), ref=ref)
                if c["status"] == "available"
                else stock(
                    reason="MSC closing balance requires complete approved entitlement and receipts"
                )
            )
        ni = deepcopy(cash_lines["net-income"]["values"][month])
        ni["source_refs"] = [ref] if ref else []
        earnings.append((month, 1, ni))
        rows["current_earnings"][month] = combine(earnings)
        # Child subtotals precede their parents in evaluation; each amount contributes once.
        for row in sorted(layout, key=lambda r: r["depth"], reverse=True):
            if row["components"]:
                rows[row["id"]][month] = combine(
                    [(c["id"], c["sign"], rows[c["id"]][month]) for c in row["components"]]
                )
        # Root totals depend on the fully calculated section components.
        for key in ["total_assets", "total_liabilities", "total_equity", "liabilities_equity"]:
            row = next(r for r in layout if r["id"] == key)
            rows[key][month] = combine(
                [(c["id"], c["sign"], rows[c["id"]][month]) for c in row["components"]]
            )
    latest = next((p["key"] for p in reversed(periods) if p["state"] != "future"), None)
    result = dict(
        schema_version="balance-sheet.v1",
        policy_version=POLICY,
        prime="spark",
        year=year,
        as_of=str(as_of),
        periods=periods,
        lines=[
            {
                **r,
                "values": rows[r["id"]],
                "total": deepcopy(rows[r["id"]][latest]) if latest else stock(),
                "aggregation": "last",
            }
            for r in layout
        ],
        total_label="Latest",
        source_manifest=manifest,
        selection=encode(
            dict(policy_version=POLICY, prime="spark", year=year, as_of=str(as_of), months=chosen)
        ),
        methodology=dict(
            stock_basis="Closing positions, including principal and earned yield; not cumulative cash flows",
            cash="Same measured cash perimeter as the cash flow statement; overlapping raw cash venues excluded",
            savings="Gross closing vault claims, including principal and accrued interest; ETH at closing USD price. Controlled-holder share eliminations remain unsourced",
            rewards="Positive outstanding rewards are receivables; negative accruals are prepaid income liabilities",
            equity="Current-year net income from cash-flow income dependencies; opening equity and contributed capital remain unsourced",
            sky_direct="Nonzero mixed-ownership positions remain unavailable until their closing ownership split is proven",
            completeness="Known subtotals only. Unmapped assets, Sky debt, opening equity and payables remain TBD; equity is never a balancing plug",
        ),
    )
    result["report_hash"] = digest(result)
    return result
