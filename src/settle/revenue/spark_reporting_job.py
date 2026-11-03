"""Publish independent monthly Spark reporting inputs, with a daily MTD refresh."""
import argparse
import calendar
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

from ..domain.config import load_prime_by_id
from ..domain.period import Month, Period
from ..domain.primes import Chain
from ..extract.hypersync_store import _reorg_margin
from ..store.db import connect
from . import spark_reporting_store, store
from .spark_onchain import WETH, certified_boundary, collect


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


def calculate(cutoff):
    prime = load_prime_by_id('spark')
    first = cutoff.replace(day=1)
    chains = [*WETH, 'avalanche_c']
    def pins(day):
        def block(chain):
            return Chain(chain), certified_boundary(chain, day, _reorg_margin())
        with ThreadPoolExecutor(max_workers=6) as pool:
            return dict(pool.map(block, chains))
    opening, closing = pins(first - timedelta(days=1)), pins(cutoff)
    period = Period.from_month(Month(cutoff.year, cutoff.month), closing, as_of=cutoff)
    document = collect(prime, period, opening, progress=lambda component: print(
        json.dumps({'cutoff': str(cutoff), 'stage': component}), flush=True))
    document.update(opening_pins={str(k): v for k, v in opening.items()}, closing_pins={str(k): v for k, v in closing.items()})
    return document


def main():
    from dotenv import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from', dest='start', type=date.fromisoformat)
    today = datetime.now(UTC).date()
    parser.add_argument('--to', dest='end', type=date.fromisoformat, default=today - timedelta(days=1))
    parser.add_argument('--schema-only', action='store_true')
    args = parser.parse_args()
    start = args.start or args.end.replace(day=1) - timedelta(days=1)
    if not start <= args.end < today:
        parser.error('window must contain completed UTC days')
    versions = store.capture_versions()
    failed = False
    with connect(autocommit=True) as conn:
        store.apply_schema(conn)
        if args.schema_only:
            return
        for cutoff in reversed(month_dates(start, args.end)):
            try:
                document = calculate(cutoff)
                revision = spark_reporting_store.publish(conn, document, code_version=versions.code,
                                                          configuration_version=versions.configuration)
                parts = [document['reserve_fees'], *document['curator_fees'].values(), *document['gains'].values(), *document['native_supplies'].values()]
                status = 'partial' if document['errors'] or any(part['status'] != 'available' for part in parts) else 'succeeded'
                print(json.dumps({'cutoff': str(cutoff), 'revision': revision, 'status': status,
                                  'errors': document['errors']}), flush=True)
                failed |= bool(document['errors'])
            except Exception as exc:
                print(json.dumps({'cutoff': str(cutoff), 'status': 'failed', 'error_type': type(exc).__name__}), flush=True)
                failed = True
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
