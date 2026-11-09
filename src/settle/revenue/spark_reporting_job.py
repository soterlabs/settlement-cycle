"""Publish independent monthly Spark reporting inputs, with a daily MTD refresh."""

import argparse
import calendar
import json
import os
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


def calculate(cutoff, *, daily_pins=None, native_venues=None, review_inputs=False):
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
    source = Source(prime, period, opening, daily_pins=daily_pins)
    source.review_scope = review_inputs
    document = collect(
        prime,
        period,
        opening,
        source=source,
        native_venues=native_venues,
        progress=lambda component: print(
            json.dumps({"cutoff": str(cutoff), "stage": component}), flush=True
        ),
    )
    document.update(
        opening_pins={str(k): v for k, v in opening.items()},
        closing_pins={str(k): v for k, v in closing.items()},
    )
    if review_inputs:
        add_review_inputs(document, source)
    return document


def add_review_inputs(document, source):
    """Worker-only economic attribution; failures remain explicitly incomplete."""
    from .spark_onchain import CURATORS, curator_fees
    from .cash_flow.config import load as cash_config
    from .spark_sde_cash import collect as sde_cash
    from .spark_sky_staking import collect as sky_staking

    try:
        document["sky_staking"] = sky_staking(source)
    except Exception as exc:
        document["sky_staking"] = {"status": "unavailable", "error": type(exc).__name__}

    wallets = cash_config(review=True)["wallets"]
    document["curator_attribution"] = {}
    document["curator_attribution_errors"] = {}
    for spec in CURATORS:
        try:
            with localcontext() as ctx:
                ctx.prec = 100
                document["curator_attribution"][spec[0]] = curator_fees(
                    source, spec, owned_wallets=list(wallets[spec[1]]))
        except Exception as exc:
            document["curator_attribution_errors"][spec[0]] = type(exc).__name__
    document["sde_cash"] = sde_cash(source, document)


def saved_daily_pins(conn, cutoff):
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT DISTINCT ON (cutoff) cutoff,closing_pins FROM revenue_results WHERE prime='spark' AND cutoff BETWEEN %s AND %s ORDER BY cutoff,publication_order DESC",
            (cutoff.replace(day=1), cutoff),
        )
        return {str(row["cutoff"]): row["closing_pins"] for row in cur.fetchall()}


def complete_month(conn, cutoff, *, source=None, accounting_only=False, sde_cash_only=False, review_inputs=False):
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
    saved = deepcopy(saved)
    result = deepcopy(old["document"])
    for name in ("opening_pins", "closing_pins"):
        retained = result.get(name, {})
        if any(chain in saved[name] and saved[name][chain] != pin for chain, pin in retained.items()):
            raise ValueError("Existing reporting inputs use different source boundaries")
        # Reporting may already contain an independently certified new chain
        # that predates its addition to revenue_results. Preserve those pins;
        # absence in the older revenue row is not a conflicting boundary.
        saved[name].update(retained)
        result[name] = deepcopy(saved[name])
    # Older publications predate Robinhood coverage. Preserve every retained
    # chain boundary and independently certify only newly required chains.
    for name, day in (("opening_pins", cutoff.replace(day=1) - timedelta(days=1)), ("closing_pins", cutoff)):
        for chain in (*WETH, "avalanche_c", "robinhood"):
            if chain not in saved[name]:
                saved[name][chain] = certified_boundary(chain, day, _reorg_margin())
    prime = load_prime_by_id("spark")
    period = Period.from_month(
        Month(cutoff.year, cutoff.month),
        {Chain(k): v for k, v in saved["closing_pins"].items()},
        as_of=cutoff,
    )
    source = source or Source(
        prime, period, saved["opening_pins"], daily_pins=saved_daily_pins(conn, cutoff)
    )
    source.review_scope = review_inputs
    if review_inputs:
        add_review_inputs(result, source)
    if sde_cash_only or review_inputs:
        from .spark_sde_cash import collect as sde_cash
        result.update(opening_pins=deepcopy(saved["opening_pins"]), closing_pins=deepcopy(saved["closing_pins"]))
        if not review_inputs:
            result["sde_cash"] = sde_cash(source, old["document"])
        result["completion_source_revision"] = old["revision_id"]
        return result
    if accounting_only:
        # The emitted document must describe the same complete pin set as the
        # source, including any chain independently certified above.
        result.update(opening_pins=deepcopy(saved["opening_pins"]),
                      closing_pins=deepcopy(saved["closing_pins"]))
        from .spark_treasury import collect as treasury
        from .spark_ownership import collect as ownership
        result["treasury"] = treasury(source)
        result["ownership"] = ownership(source, retained=result)
        result["completion_source_revision"] = saved["revision_id"]
        return result
    return complete_document(old, source, source_revision=saved["revision_id"])


def complete_document(old, source, *, source_revision=None):
    """Extend one immutable monthly source using independently pinned reads."""
    from .spark_completion import collect as complete

    result = deepcopy(old["document"])
    for name in ("opening_pins", "closing_pins"):
        actual = getattr(source, name)
        if any(actual.get(chain) != pin for chain, pin in result.get(name, {}).items()):
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
    from .spark_treasury import collect as treasury
    try:
        result["treasury"] = treasury(source)
    except Exception as exc:
        result["treasury"] = {"errors": {"collection": type(exc).__name__}}
    from .spark_ownership import collect as ownership
    try:
        result["ownership"] = ownership(source, retained=result)
    except Exception as exc:
        result["ownership"] = {"errors": {"collection": type(exc).__name__}}
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
    parser.add_argument("--accounting-only", action="store_true",
        help="Refresh treasury and historical ownership inputs only; preserve existing native/issuer accruals and MSC")
    parser.add_argument("--sde-cash-only", action="store_true", help="Add authenticated SDE liquidity principal evidence; preserve other income inputs")
    parser.add_argument("--review-inputs", action="store_true", help="Refresh curator attribution, authenticated SDE principal and reviewed SKY staking cash income")
    args = parser.parse_args()
    if args.review_inputs and (args.sde_cash_only or args.accounting_only or args.complete_only):
        parser.error("Select only one completion mode")
    if args.sde_cash_only and (args.accounting_only or args.complete_only):
        parser.error("Select only one completion mode")
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
                    complete_month(conn, cutoff, accounting_only=args.accounting_only, sde_cash_only=args.sde_cash_only, review_inputs=args.review_inputs)
                    if args.complete_only or args.accounting_only or args.sde_cash_only or args.review_inputs
                    else calculate(cutoff, daily_pins=saved_daily_pins(conn, cutoff),
                                   review_inputs=os.environ.get("SPARK_REPORTING_REVIEW_ENABLED") == "1")
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
                    or document.get("sde_cash", {}).get("errors")
                    or document.get("curator_attribution_errors")
                    or document.get("treasury", {}).get("errors")
                    or document.get("ownership", {}).get("errors")
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
                    or document.get("sde_cash", {}).get("errors")
                    or document.get("curator_attribution_errors")
                    or document.get("treasury", {}).get("errors")
                    or document.get("ownership", {}).get("errors")
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
