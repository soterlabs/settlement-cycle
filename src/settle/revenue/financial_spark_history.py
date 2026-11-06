"""Historical source completion, without principal or daily MSC replay.

Published monthly returns are an explicitly estimated fallback for native
supply, never an invented zero or a replacement for an available native source.
Older report-selection policies retain their original missing cells.
"""

from decimal import Decimal, localcontext

from .financial import metric, number
from .financial_allocations import ALLOCATIONS
from .financial_amms import money, monthly_rows
from .financial_spark import saved_component
from .financial_spark_statement import RWA, SUSDS_KEYS
from .statement_rollups import combine


def extend(statement, monthly, supplements):
    lookup = {r["id"]: r for r in statement["lines"]}
    for period, published, saved in zip(statement["periods"], monthly, supplements, strict=True):
        if period["state"] == "future":
            continue
        month = period["key"]
        if published:
            try:
                rows = monthly_rows(published)
            except (ValueError, KeyError):
                rows = []

            def read(venue, rows=rows):
                matches = [r for r in rows if r["Venue"] == venue]
                if len(matches) != 1:
                    return None
                row = matches[0]
                try:
                    opening, closing, capital, gross, total, sky = (
                        money(row[k])
                        for k in (
                            "value_som",
                            "value_eom",
                            "period_inflow",
                            "actual_rev",
                            "revenue",
                            "sd_revenue",
                        )
                    )
                    share = number(row["sd_share"].removesuffix("%")) / 100
                    if (
                        not 0 <= share <= 1
                        or abs(closing - opening - capital - gross) > Decimal("0.03")
                        or abs(gross * share - sky) > Decimal("0.03")
                    ):
                        return None
                    return gross - sky, total - (gross - sky)
                except (KeyError, ValueError):
                    return None

            def fallback(key, value, *, receipt=False, month=month, published=published):
                if key not in lookup:
                    return
                cell = lookup[key]["values"][month]
                if cell.get("value") is not None or cell.get("known_value") is not None:
                    return
                cell = metric(
                    value,
                    basis="published_monthly_external_reward_receipts"
                    if receipt
                    else "published_monthly_native_return_excluding_capital",
                )
                cell.update(
                    source_ref=published["revision_id"],
                    source_paths=[published["source"]["url"] + "#per-venue"],
                    period_start=month + "-01",
                    period_end=published["cutoff"],
                    recognition="receipt" if receipt else "investment_return",
                    quality="supported" if receipt else "estimate",
                )
                lookup[key]["values"][month] = cell

            for spec in ALLOCATIONS["spark"]:
                amounts = read(spec.venue)
                if amounts is None:
                    continue
                fallback("investment.venue." + spec.venue, str(amounts[0]))
                if spec.family == "Aave v3":
                    fallback("rewards.lending." + spec.key, str(amounts[1]), receipt=True)
            for venue, (key, _) in RWA.items():
                amounts = read(venue)
                if amounts and amounts[1] == 0:
                    fallback("investment.rwa." + key, str(amounts[0]))
            for venue, chain in SUSDS_KEYS.items():
                amounts = read(venue)
                if amounts is not None:
                    fallback("investment.susds." + chain, str(amounts[0]))
            # Refresh business subtotals from the completed venue leaves.
            for row in statement["lines"]:
                if row["id"].startswith("investment.") and row.get("components"):
                    terms = [
                        (c["id"], c["sign"], lookup[c["id"]]["values"][month])
                        for c in row["components"]
                    ]
                    row["values"][month] = {
                        **combine(terms),
                        "component_ids": [c["id"] for c in row["components"]],
                    }
        if not saved:
            continue
        doc = saved["document"]
        for source, key, recognition in [
            (
                doc.get("statement_allocations", {}).get("anchorage.receipts"),
                "investment.anchorage",
                "receipt",
            ),
            *(
                (
                    doc.get("issuer_income", {}).get(program),
                    "rewards.issuer." + program,
                    "receipt" if program == "usdg" else "accrual",
                )
                for program in ("rlusd", "pyusd", "usdg")
            ),
        ]:
            current = lookup[key]["values"][month]
            current_end = current.get("period_end") or statement["source_manifest"].get(
                current.get("source_ref"), {}
            ).get("cutoff", "")
            if (
                source
                and source.get("status") == "available"
                and (current.get("value") is None or saved["cutoff"] > current_end)
            ):
                lookup[key]["values"][month] = saved_component(
                    source,
                    "value",
                    saved,
                    recognition=recognition,
                    expected=period["expected_cutoff"],
                )
        for venue, suffix in (("S61", "pyusd_usds"), ("S62", "usdt_usds"), ("S66", "rlusd_usds")):
            key = "investment.amm.uniswap_v4." + suffix
            fee = doc.get("amm_fees", {}).get(venue, {})
            proof = fee.get("capital_reconciliation", {})
            current = lookup[key]["values"][month]
            if proof.get("status") != "available" or (
                current.get("quality") == "supported" and current.get("status") == "available"
            ):
                continue
            split = current.get("allocation_split")
            ref = current.get("source_ref")
            gross = number(proof["gross_return_usd"])
            empty = (
                all(
                    number(proof[k]) == 0
                    for k in (
                        "opening_position_usd",
                        "closing_position_usd",
                        "net_capital_added_usd",
                        "gross_return_usd",
                    )
                )
                and number(fee["value"]) == 0
            )
            if split is not None and current.get("period_end") == saved["cutoff"] and ref:
                share = number(split["sky_share"])
                share_basis = split["sky_share_basis"]
            elif empty:
                share = Decimal(0)  # Allocation is immaterial on a proved empty book.
                share_basis = "not_applicable_empty_position"
                ref = None
            else:
                continue
            with localcontext() as ctx:
                ctx.prec = 60
                sky = gross * share
                native = gross - sky
            item = saved_component(
                {**fee, "value": str(native), "basis": proof["basis"]},
                "value",
                saved,
                recognition="investment_return",
                expected=period["expected_cutoff"],
            )
            item["allocation_split"] = {
                "gross_income_usd": str(gross),
                "sky_income_usd": str(sky),
                "prime_income_usd": str(native),
                "sky_share": str(share),
                "sky_share_basis": share_basis,
                "rounding_delta_usd": "0",
            }
            item["quality"] = "supported" if share in (0, 1) else "estimate"
            if ref:
                item.pop("source_ref", None)
                item["source_refs"] = [ref, saved["revision_id"]]
            lookup[key]["values"][month] = item
            ledgers = statement.setdefault("spark_amm_ledger", [])
            ledger = next(
                (r for r in ledgers if r["month"] == month and r["venue_id"] == venue), None
            )
            if ledger is None:
                ledger = {
                    "month": month,
                    "venue_id": venue,
                    "metric_id": key,
                    "label": suffix.upper().replace("_", " / "),
                    "family": "Uniswap v4",
                    "external_rewards_excluded_usd": "0",
                    "issuer_incentives_included": False,
                    "embedded_susds_return_usd": "0",
                    "source_display_precision": "full_source_precision",
                }
                ledgers.append(ledger)
            ledger.update(
                {
                    k: proof[k]
                    for k in (
                        "opening_position_usd",
                        "closing_position_usd",
                        "net_capital_added_usd",
                    )
                }
            )
            ledger.update(
                income=item,
                gross_position_return_usd=str(gross),
                sky_return_usd=str(sky),
                spark_position_return_usd=str(native),
                fee_coverage="accrued_V4_fees_reconciled",
                accrued_trading_fees=fee,
                capital_reconciliation=proof,
                through=saved["cutoff"],
                source_ref=saved["revision_id"],
            )
    for ledger in statement.get("spark_allocation_ledger", []):
        key = "investment.venue." + ledger["venue_id"]
        ledger["native_revenue_usd"] = lookup[key]["values"][ledger["month"]]
