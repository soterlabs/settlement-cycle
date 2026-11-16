"""Restartable one-month reporting averages. Never runs the MSC pipeline.

Usage: python -m settle.revenue.margin_balance_job --month 2026-09
       --cache-dir /data/spark-margin-balances --publish
"""

import argparse
import fcntl
import gzip
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from dotenv import load_dotenv

from ..domain.config import load_prime_by_id
from ..store.db import connect
from . import allocations, balance_sheet_store
from . import balance_sheet_sources as stocks
from . import margin_balances as model
from .cash_flow.config import load as cash_config
from .spark_onchain import market_price
from .spark_sky_staking import FARM, URN
from .spark_steakhouse import USDC, VAULT
from .verification import digest

SKY = "0x56072c95faa701256059aa122697b133aded9279"


def universe():
    prime = load_prime_by_id("spark")
    positions = [
        v.id
        for v in prime.venues
        if v.reporting_view.enabled
        and str(v.pricing_category) != "S2"
        and v.id not in {"S23", "S67", "S68", "S69"}
    ]
    savings = [v.id for v in prime.venues if str(v.pricing_category) == "S2"] + [
        r[0] for r in stocks.EXTRA_SAVINGS
    ]
    extra = (
        ["psm." + str(ch) for ch in prime.psm]
        + ["idle." + k for k in savings]
        + ["alm_eth", "pau_susdc", "steakhouse", "sky_staking"]
    )
    return {
        "assets": sorted([*positions, "sparklend_weth", *extra]),
        "funding": sorted(
            [v.id for v in prime.venues if str(v.pricing_category) == "S2"] + ["sky_debt"]
        ),
    }


def supplement(document):
    """Missing investment stocks; the core B/S collector already values ALM cash.

    Savings idle marked 'already in measured cash' must be read here, since this
    allocation-book denominator does not add the separate cash-flow cash subtotal.
    """
    prime = load_prime_by_id("spark")
    reader = stocks.Reader(document["pins"])
    cfg = cash_config()
    result = {}
    savings = [
        (
            v.id,
            str(v.chain),
            v.token.address.hex,
            v.underlying.address.hex,
            v.underlying.decimals,
            v.underlying.symbol,
        )
        for v in prime.venues
        if str(v.pricing_category) == "S2"
    ]
    for key, ch, vault, asset, decimals, symbol in savings:
        if document["extra_assets"]["idle." + key].get("basis") == "cash_perimeter_exclusion":
            result["idle." + key] = str(
                D(reader.balance(ch, asset, vault)) / 10**decimals * reader.price(symbol)
            )
    values = []
    for owner in sorted(cfg["wallets"]["ethereum"]):
        shares = (
            reader.balance("ethereum", VAULT, owner)
            if reader.code("ethereum", VAULT) != "0x"
            else 0
        )
        if shares:
            if reader.number("ethereum", VAULT, "asset()") != int(USDC, 16):
                raise ValueError("Steakhouse asset differs")
            values.append(
                D(reader.number("ethereum", VAULT, "previewRedeem(uint256)", shares)) / 10**6
            )
    result["steakhouse"] = str(sum(values, D(0)))
    units = reader.balance("ethereum", FARM, URN) if reader.code("ethereum", FARM) != "0x" else 0
    quote = (
        market_price("ethereum:" + SKY, document["pins"]["ethereum"]["timestamp"])
        if units
        else None
    )
    result["sky_staking"] = str(D(units) / 10**18 * D(quote["price"])) if units else "0"
    return {"values": result, "reads": reader.reads, "sky_price": quote}


def daily_row(document, extra):
    balance_sheet_store.validate(document)

    def value(cell):
        if cell["status"] != "available":
            raise ValueError("Incomplete daily stocks")
        return str(model.number(cell["value"]))

    assets = {k: value(v) for k, v in document["positions"].items() if k != "S23"}
    assets.update({k: value(v) for k, v in document["extra_assets"].items()})
    expected_supplement = {"steakhouse", "sky_staking"} | {
        k
        for k, v in document["extra_assets"].items()
        if v.get("basis") == "cash_perimeter_exclusion"
    }
    if set(extra["values"]) != expected_supplement:
        raise ValueError("Savings idle supplement coverage differs")
    assets.update({k: str(model.number(v)) for k, v in extra["values"].items()})
    # The current reporting book has no non-LP Sky-direct claim in these months.
    # Fail closed if one appears: the ownership adapter must explicitly remove it.
    for venue in ("S20", "S21"):
        if D(assets.get(venue, "0")):
            raise ValueError("Nonzero historical Sky-direct RWA requires ownership attribution")
    funding = {
        k: value(document["liabilities"][k]) for k in universe()["funding"] if k != "sky_debt"
    }
    funding["sky_debt"] = value(document["sky_debt"])
    expected = universe()
    if set(assets) != set(expected["assets"]) or set(funding) != set(expected["funding"]):
        raise ValueError("Stock collection differs from the configured allocation perimeter")
    return dict(
        date=document["cutoff"],
        assets=assets,
        funding=funding,
        evidence_hash=digest({"stocks": document, "supplement": extra}),
    )


def atomic_json(path, document):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt") as f:
        json.dump(document, f)
    tmp.replace(path)


def run(month, cache, *, publish=False, workers=2):
    model.days(month)
    folder = Path(cache) / month
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _run(month, cache, publish=publish, workers=workers)


def _run(month, cache, *, publish=False, workers=2):
    dates = model.days(month)
    if date.fromisoformat(dates[-1]) >= date.today():
        raise ValueError("Only closed calendar months")
    cache = Path(cache) / month
    cache.mkdir(parents=True, exist_ok=True)
    with connect(autocommit=True) as conn:
        facility = allocations.read(conn, "spark", month)
        if not facility:
            raise ValueError("Published facility principal is required")
        loans = [r for r in facility["allocations"] if r["venue_id"] == "S23"]
        if (
            len(loans) != 1
            or loans[0].get("yield_denominator")
            != "daily outstanding facility principal; not escrow cash"
        ):
            raise ValueError("Facility evidence must measure daily outstanding principal")
        facility_average = loans[0]["average_yield_exposure_usd"]
        retained = balance_sheet_store.year_rows(conn, 2026)
        # Candidate pins avoid binary-searching dates already captured by the daily job.
        pins = dict(
            conn.execute(
                "SELECT DISTINCT ON(cutoff) cutoff,closing_pins FROM revenue_results WHERE prime='spark' AND cutoff BETWEEN %s AND %s ORDER BY cutoff,publication_order DESC",
                (dates[0], dates[-1]),
            ).fetchall()
        )
    sources = {r["cutoff"]: r for r in retained}

    def process(day):
        target = cache / (day + ".json.gz")
        if target.exists():
            with gzip.open(target, "rt") as f:
                saved = json.load(f)
            doc, extra = saved["stocks"], saved["supplement"]
        else:
            retained_doc = sources.get(day, {}).get("document")
            stock_path = cache / (day + ".stocks.json.gz")
            if stock_path.exists():
                with gzip.open(stock_path, "rt") as f:
                    retained_doc = json.load(f)
            doc = retained_doc or stocks.collect(
                date.fromisoformat(day),
                candidates=pins.get(date.fromisoformat(day)),
                progress=lambda stage: print(json.dumps({"date": day, "stage": stage}), flush=True),
            )
            balance_sheet_store.validate(doc)
            if set(doc["errors"]) - {"S23"}:
                raise ValueError("Daily stock collection incomplete: " + str(doc["errors"]))
            atomic_json(stock_path, doc)
            with localcontext() as ctx:
                ctx.prec = 100
                extra = supplement(doc)
            daily_row(doc, extra)
            atomic_json(target, {"stocks": doc, "supplement": extra})
        row = daily_row(doc, extra)
        if row["date"] != day:
            raise ValueError("Cached day differs")
        print(json.dumps({"date": day, "status": "complete"}), flush=True)
        return row

    with ThreadPoolExecutor(max_workers=workers) as pool:
        daily = list(pool.map(process, dates))
    refs = {
        "facility_publication": facility["revision_id"],
        "allocation_perimeter": digest(universe()),
    }
    document = model.build(month, daily, facility_average, source_refs=refs, universe=universe())
    atomic_json(cache / "monthly.json.gz", document)
    if publish:
        code = (
            os.environ.get("RAILWAY_GIT_COMMIT_SHA")
            or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        )
        evidence = {}
        for day in dates:
            with gzip.open(cache / (day + ".json.gz"), "rt") as f:
                evidence[day] = json.load(f)
        with connect(autocommit=True) as conn:
            with conn.transaction():
                revision = model.publish(conn, document, code, evidence)
        print(
            json.dumps(
                {
                    "month": month,
                    "revision": revision,
                    "assets": document["average_allocated_assets"],
                    "funding": document["average_funding"],
                }
            ),
            flush=True,
        )
    return document


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--workers", type=int, choices=[1, 2, 3], default=2)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    run(args.month, args.cache_dir, publish=args.publish, workers=args.workers)


if __name__ == "__main__":
    main()
