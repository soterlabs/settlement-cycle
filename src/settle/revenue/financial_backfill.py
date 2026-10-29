"""Backfill financial reporting from retained saved results, with no provider reads.

python -m settle.revenue.financial_backfill --from 2026-09-01 --to 2026-10-27
"""
import argparse
import json
from datetime import UTC, date, datetime, timedelta

from ..store.db import connect
from . import financial_store, store
from .financial import POLICY_VERSION
from .verification import PRIMES


def run(conn, prime, start, end):
    # Stable latest-source selection at the start; never hold a source-worker
    # lock or refetch inputs. A subsequent daily source correction publishes anew.
    sources = conn.execute('''SELECT DISTINCT ON (cutoff) cutoff,revision_id FROM revenue_results
        WHERE prime=%s AND cutoff BETWEEN %s AND %s
        ORDER BY cutoff,publication_order DESC''', (prime, start, end)).fetchall()
    counts = {'published': 0, 'reused': 0, 'failed': 0}
    for cutoff, revision in sources:
        if financial_store.read(conn, prime, cutoff=cutoff, source_revision=revision):
            counts['reused'] += 1
            continue
        state = financial_store.publish_revision(conn, prime, revision)
        counts['published' if state == 'succeeded' else 'failed'] += 1
    return {'prime': prime, 'from': str(start), 'to': str(end), 'policy_version': POLICY_VERSION,
            'source_dates': len(sources),
            'first_cutoff': str(min(d for d, _ in sources)) if sources else None,
            'last_cutoff': str(max(d for d, _ in sources)) if sources else None, **counts}


def main():
    from dotenv import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from', dest='start', type=date.fromisoformat, required=True)
    parser.add_argument('--to', dest='end', type=date.fromisoformat,
                        default=datetime.now(UTC).date() - timedelta(days=1))
    parser.add_argument('--prime', choices=PRIMES, action='append')
    args = parser.parse_args()
    if not args.start <= args.end < datetime.now(UTC).date():
        parser.error('window must contain completed UTC days')
    failed = False
    with connect(autocommit=True) as conn:
        store.apply_schema(conn)
        for prime in args.prime or PRIMES:
            report = run(conn, prime, args.start, args.end)
            print(json.dumps(report), flush=True)
            failed |= report['failed'] > 0
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
