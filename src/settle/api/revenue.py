"""Read-only daily estimates; no calculation or provider calls on API requests."""
from datetime import UTC, date, datetime, timedelta

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row

from ..revenue import store
from ..revenue.verification import PRIMES, canonical


def today():
    return datetime.now(UTC).date()


def latest_attempt(conn, prime):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('''SELECT attempt_id, cutoff, status, started_at, finished_at,
            error_type, revision_id FROM revenue_attempts WHERE prime=%s
            ORDER BY started_at DESC, attempt_id DESC LIMIT 1''', (prime,))
        row = cur.fetchone()
    if row:
        row['attempt_id'] = str(row['attempt_id'])
    return canonical(row)


class RevenueReaderMixin:
    def revenue_bundle(self, prime, *, cutoff=None, revision=None):
        with self._pool.connection() as conn:
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                return (store.read(conn, prime, cutoff=cutoff, revision=revision), latest_attempt(conn, prime))

    def revenue_history(self, prime, *, start, end, limit):
        with self._pool.connection() as conn:
            return store.history(conn, prime, start=start, end=end, limit=limit)

    def revenue_revisions(self, prime, cutoff, *, limit):
        with self._pool.connection() as conn:
            return store.revisions(conn, prime, cutoff, limit=limit)

    def revenue_status(self):
        with self._pool.connection() as conn:
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                return {p: (store.read(conn, p), latest_attempt(conn, p)) for p in PRIMES}


def freshness(record, expected):
    actual = date.fromisoformat(record['cutoff']) if record else None
    return {'expected_cutoff': expected.isoformat(),
            'actual_cutoff': actual.isoformat() if actual else None,
            'stale': actual is None or actual < expected}


def register(app, get_reader, document_response):
    def validate(prime, cutoff=None):
        if prime not in PRIMES:
            raise HTTPException(404, 'Unknown prime')
        if cutoff is not None and cutoff >= today():
            raise HTTPException(422, 'cutoff must be a completed UTC day')

    def read_or_unavailable(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            raise HTTPException(503, 'Daily revenue store unavailable', headers={'Cache-Control': 'no-store'}) from None

    def result_document(request, r, prime, cutoff=None, revision=None):
        validate(prime, cutoff)
        record, attempt = read_or_unavailable(r.revenue_bundle, prime, cutoff=cutoff, revision=revision)
        if record is None:
            raise HTTPException(404, 'No published daily estimate for this selection; use monthly data',
                                headers={'Cache-Control': 'no-store'})
        return document_response(request, {
            'schema_version': '1.0', 'prime': prime, 'cadence': 'daily',
            'estimate_basis': 'month_to_date', 'data': record,
            'freshness': freshness(record, cutoff or today()-timedelta(days=1)),
            'latest_attempt': attempt,
        })

    @app.get('/v1/revenue/status')
    def status(r=Depends(get_reader)):  # noqa: B008
        snapshots = read_or_unavailable(r.revenue_status)
        expected = today()-timedelta(days=1)
        states = {}
        for prime, (record, attempt) in snapshots.items():
            states[prime] = {**freshness(record, expected), 'latest_attempt': attempt}
        ready = (set(states) == set(PRIMES) and all(not s['stale'] and
                 (not s['latest_attempt'] or s['latest_attempt']['status'] not in {'failed', 'abandoned'})
                 for s in states.values()))
        return JSONResponse({'cadence': 'daily', 'ready': ready, 'primes': states},
                            status_code=200 if ready else 503, headers={'Cache-Control': 'no-store'})

    @app.get('/v1/revenue/{prime}/latest')
    def latest(prime: str, request: Request, r=Depends(get_reader)):  # noqa: B008
        return result_document(request, r, prime)

    @app.get('/v1/revenue/{prime}/at/{cutoff}')
    def at(prime: str, cutoff: date, request: Request,
           revision: str | None = Query(None, pattern=r'^[0-9a-f]{64}$'),
           r=Depends(get_reader)):  # noqa: B008
        return result_document(request, r, prime, cutoff, revision)

    @app.get('/v1/revenue/{prime}/history')
    def history(prime: str, request: Request, start: date | None = None, end: date | None = None,
                limit: int = Query(90, ge=1, le=90), r=Depends(get_reader)):  # noqa: B008
        end = end or today()-timedelta(days=1)
        start = start or end-timedelta(days=min(89, end.toordinal()-1))
        validate(prime, end)
        if start > end or (end-start).days >= 90:
            raise HTTPException(422, 'history window must contain 1 to 90 days')
        rows = read_or_unavailable(r.revenue_history, prime, start=start, end=end, limit=limit)
        return document_response(request, {'schema_version': '1.0', 'prime': prime,
            'cadence': 'daily', 'estimate_basis': 'month_to_date', 'results': rows,
            'order': 'cutoff descending', 'limit': limit, 'start': str(start), 'end': str(end),
            'missing_dates': 'unavailable; use canonical monthly data'})

    @app.get('/v1/revenue/{prime}/revisions/{cutoff}')
    def revisions(prime: str, cutoff: date, request: Request,
                  limit: int = Query(100, ge=1, le=100), r=Depends(get_reader)):  # noqa: B008
        validate(prime, cutoff)
        rows = read_or_unavailable(r.revenue_revisions, prime, cutoff, limit=limit)
        return document_response(request, {'schema_version': '1.0', 'prime': prime,
            'cutoff': str(cutoff), 'revisions': rows, 'limit': limit, 'order': 'newest publication first'})
