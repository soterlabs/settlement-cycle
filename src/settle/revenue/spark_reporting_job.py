"""Publish independent monthly Spark reporting inputs, with a daily MTD refresh."""

import argparse
import calendar
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import localcontext

from ..domain.config import load_prime_by_id
from ..domain.period import Month, Period
from ..domain.primes import Chain
from ..extract.hypersync_store import _reorg_margin
from ..store.db import connect
from . import spark_reporting_store, store
from .spark_onchain import (
    WETH,
    Source,
    certified_boundary,
    certified_saved_boundary,
    collect,
    gains,
)


def month_dates(start, end):
    dates = set()
    cursor = start.replace(day=1)
    while cursor <= end:
        closing = cursor.replace(day=calendar.monthrange(cursor.year, cursor.month)[1])
        if start <= closing <= end:
            dates.add(closing)
        cursor = closing + timedelta(days=1)
    dates.add(end)
    return sorted(dates)


def calculate(cutoff, *, daily_pins=None, native_venues=None):
    prime = load_prime_by_id("spark")
    first = cutoff.replace(day=1)
    chains = [*WETH, "avalanche_c", "robinhood"]

    def pins(day):
        def block(chain):
            candidate = (daily_pins or {}).get(str(day), {}).get(chain)
            return Chain(chain), (
                certified_saved_boundary(chain, day, candidate)
                if candidate is not None
                else certified_boundary(chain, day, _reorg_margin())
            )

        with ThreadPoolExecutor(max_workers=6) as pool:
            return dict(pool.map(block, chains))

    opening, closing = pins(first - timedelta(days=1)), pins(cutoff)
    period = Period.from_month(Month(cutoff.year, cutoff.month), closing, as_of=cutoff)
    document = collect(
        prime,
        period,
        opening,
        source=Source(prime, period, opening, daily_pins=daily_pins),
        native_venues=native_venues,
        progress=lambda component: print(
            json.dumps({"cutoff": str(cutoff), "stage": component}), flush=True
        ),
    )
    document.update(
        opening_pins={str(k): v for k, v in opening.items()},
        closing_pins={str(k): v for k, v in closing.items()},
    )
    return document


def saved_daily_pins(conn, cutoff):
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT DISTINCT ON (cutoff) cutoff,closing_pins FROM revenue_results WHERE prime='spark' AND cutoff BETWEEN %s AND %s ORDER BY cutoff,publication_order DESC",
            (cutoff.replace(day=1), cutoff),
        )
        return {str(row["cutoff"]): row["closing_pins"] for row in cur.fetchall()}


def complete_month(conn, cutoff, *, source=None):
    """Fill reporting gaps for one saved month; never replay or mutate MSC."""
    saved = store.read(conn, "spark", cutoff=cutoff)
    candidates = [
        r for r in spark_reporting_store.year_rows(conn, cutoff.year) if r["cutoff"] == str(cutoff)
    ]
    if not candidates:
        raise ValueError("No Spark reporting components for the requested month")
    old = max(candidates, key=lambda r: r["publication_order"])
    saved = saved or {
        "opening_pins": old["document"]["opening_pins"],
        "closing_pins": old["document"]["closing_pins"],
        "revision_id": old["revision_id"],
    }
    result = deepcopy(old["document"])
    for name in ("opening_pins", "closing_pins"):
        if any(saved[name].get(chain) != pin for chain, pin in result.get(name, {}).items()):
            raise ValueError("Existing reporting inputs use different source boundaries")
        result[name] = saved[name]
    prime = load_prime_by_id("spark")
    period = Period.from_month(
        Month(cutoff.year, cutoff.month),
        {Chain(k): v for k, v in saved["closing_pins"].items()},
        as_of=cutoff,
    )
    source = source or Source(
        prime, period, saved["opening_pins"], daily_pins=saved_daily_pins(conn, cutoff)
    )
    return complete_document(old, source, source_revision=saved["revision_id"])


def complete_document(old, source, *, source_revision=None):
    """Extend one immutable monthly source using independently pinned reads."""
    from .spark_completion import collect as complete

    result = deepcopy(old["document"])
    for name in ("opening_pins", "closing_pins"):
        actual = getattr(source, name)
        if result.get(name) != actual:
            raise ValueError("Reporting source boundaries differ from the retained month")
        result[name] = actual
    result.update(
        complete(source, progress=lambda key: print(json.dumps({"stage": key}), flush=True))
    )
    from .allocation_revenue import SPARK_EXTRA
    from .financial_allocations import ALLOCATIONS
    from .spark_onchain import native_supply

    with localcontext() as ctx:
        ctx.prec = 60
        for spec in (*ALLOCATIONS["spark"], *SPARK_EXTRA):
            if spec.venue in ("S70", "S71", "S36"):
                result["native_supplies"][spec.venue] = native_supply(source, spec)
                result.get("errors", {}).pop("supply." + spec.venue, None)
        result["gains"]["morpho"] = gains(source, "morpho")
        result.get("errors", {}).pop("gains.morpho", None)
        from .spark_reconciliation import empty_avax_gain, net_sparklend_gain

        result["gains"]["avax"] = empty_avax_gain(source, result["gains"].get("avax", {}))
        result["gains"]["sparklend_nonstable"] = net_sparklend_gain(
            source,
            result["gains"].get("sparklend_nonstable", {}),
            result["statement_allocations"].get("sparklend.weth", {}),
            result["savings_interest"].get("S58", {}),
        )
    from .spark_monthly_income import collect as monthly_income

    income = monthly_income(
        source,
        result.get("amm_fees", {}),
        progress=lambda key: print(json.dumps({"stage": key}), flush=True),
    )
    result["issuer_income"] = income["issuer_income"]
    result["monthly_income_errors"] = income["monthly_income_errors"]
    if "anchorage.receipts" in income:
        result["statement_allocations"]["anchorage.receipts"] = income["anchorage.receipts"]
    result["extended_from"] = old["document"].get("extended_from", old["revision_id"])
    result["completion_source_revision"] = source_revision or old["revision_id"]
    return result


def main():
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", type=date.fromisoformat)
    today = datetime.now(UTC).date()
    parser.add_argument(
        "--to", dest="end", type=date.fromisoformat, default=today - timedelta(days=1)
    )
    parser.add_argument("--schema-only", action="store_true")
    parser.add_argument(
        "--complete-only",
        action="store_true",
        help="Extend existing reporting months using retained source pins; no settlement replay",
    )
    args = parser.parse_args()
    start = args.start or args.end.replace(day=1) - timedelta(days=1)
    if not start <= args.end < today:
        parser.error("window must contain completed UTC days")
    versions = store.capture_versions()
    failed = False
    with connect(autocommit=True) as conn:
        store.apply_schema(conn)
        if args.schema_only:
            return
        for cutoff in reversed(month_dates(start, args.end)):
            try:
                document = (
                    complete_month(conn, cutoff)
                    if args.complete_only
                    else calculate(cutoff, daily_pins=saved_daily_pins(conn, cutoff))
                )
                revision = spark_reporting_store.publish(
                    conn,
                    document,
                    code_version=versions.code,
                    configuration_version=versions.configuration,
                )
                parts = [
                    document["reserve_fees"],
                    *(
                        row
                        for family in (
                            "curator_fees",
                            "gains",
                            "native_supplies",
                            "savings_interest",
                            "statement_allocations",
                            "amm_fees",
                            "issuer_income",
                        )
                        for row in document.get(family, {}).values()
                    ),
                ]
                status = (
                    "partial"
                    if document["errors"]
                    or document.get("completion_errors")
                    or document.get("monthly_income_errors")
                    or any(part["status"] != "available" for part in parts)
                    else "succeeded"
                )
                print(
                    json.dumps(
                        {
                            "cutoff": str(cutoff),
                            "revision": revision,
                            "status": status,
                            "errors": document["errors"],
                        }
                    ),
                    flush=True,
                )
                failed |= bool(
                    document["errors"]
                    or document.get("completion_errors")
                    or document.get("monthly_income_errors")
                )
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "cutoff": str(cutoff),
                            "status": "failed",
                            "error_type": type(exc).__name__,
                        }
                    ),
                    flush=True,
                )
                failed = True
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
