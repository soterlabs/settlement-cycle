"""Immutable daily estimates, independently versioned from reusable raw inputs."""
from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .verification import PRIMES, canonical, digest, validate_window

_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Versions:
    code: str
    configuration: str
    inputs: str


def capture_versions():
    code = (os.environ.get('REVENUE_CODE_VERSION') or os.environ.get('RAILWAY_GIT_COMMIT_SHA')
            or os.environ.get('GITHUB_SHA'))
    if not code:
        dirty = subprocess.check_output(
            ['git', 'status', '--porcelain', '--untracked-files=all', '--', 'src', 'config'],
            cwd=_ROOT, text=True)
        if dirty:
            raise ValueError('code/config tree is dirty; commit before publishing')
        code = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=_ROOT, text=True).strip()
    files = sorted((_ROOT / 'config').rglob('*.yaml'))
    configuration = digest({str(p.relative_to(_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in files})
    return Versions(code, configuration, os.environ.get('SETTLE_INPUT_REVISION', '0'))


def apply_schema(conn):
    conn.execute((_ROOT / 'db' / 'schema_revenue.sql').read_text())


def has_venue_views(result):
    if not isinstance(result, dict):
        return False
    views = result.get('venue_views', {})
    return (isinstance(views, dict) and views.get('schema_version') == '2.0'
            and all(isinstance(views.get(view), list) for view in ('settlement', 'reporting')))


def venue_view_dates(conn, prime):
    """Only the newest revision per cutoff determines API compatibility."""
    return {row[0] for row in conn.execute('''
        SELECT cutoff FROM (
            SELECT DISTINCT ON (cutoff) cutoff, result
            FROM revenue_results WHERE prime=%s
            ORDER BY cutoff, publication_order DESC
        ) latest
        WHERE result->'venue_views'->>'schema_version' = '2.0'
          AND jsonb_typeof(result->'venue_views'->'settlement') = 'array'
          AND jsonb_typeof(result->'venue_views'->'reporting') = 'array'
        ''', (prime,))}


def publish(conn, pnl, versions: Versions, *, input_provenance=None):
    """Write within the caller's transaction. Identical retries are idempotent.

    Daily estimates remain provisional even at month-end: this publication path
    does not perform canonical monthly settlement or DR workbook enrichment.
    """
    if pnl.prime_id not in PRIMES:
        raise ValueError('unknown prime')
    validate_window(pnl.as_of)
    if not versions.code or not versions.configuration or not versions.inputs:
        raise ValueError('all result versions are required')
    opening, closing = canonical(pnl.pin_blocks_som), canonical(pnl.period.pin_blocks)
    identity = dict(prime=pnl.prime_id, cutoff=pnl.as_of.isoformat(), opening_pins=opening,
                    closing_pins=closing, code_version=versions.code,
                    configuration_version=versions.configuration, input_revision=versions.inputs)
    revision = digest(identity)
    payload = canonical(pnl)
    result_hash = digest(payload)
    with conn.cursor() as cur:
        cur.execute('''INSERT INTO revenue_results
            (revision_id, prime, cutoff, opening_pins, closing_pins, code_version,
             configuration_version, input_revision, result, result_hash, input_provenance)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING''',
            (revision, pnl.prime_id, pnl.as_of, Jsonb(opening), Jsonb(closing), versions.code,
             versions.configuration, versions.inputs, Jsonb(payload), result_hash, Jsonb(input_provenance or {})))
        cur.execute('SELECT result_hash FROM revenue_results WHERE revision_id=%s', (revision,))
        if cur.fetchone()[0] != result_hash:
            raise ValueError('same revision produced different results; correct inputs with a new revision')
    return revision


def read(conn, prime: str, *, cutoff: date | None = None, revision: str | None = None):
    clauses, params = ['prime=%s'], [prime]
    if cutoff is not None:
        clauses.append('cutoff=%s')
        params.append(cutoff)
    if revision is not None:
        clauses.append('revision_id=%s')
        params.append(revision)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT * FROM revenue_results WHERE ' + ' AND '.join(clauses)
                    + ' ORDER BY cutoff DESC, publication_order DESC LIMIT 1', params)
        row = cur.fetchone()
    return canonical(row) if row else None


def history(conn, prime: str, *, start: date, end: date, limit: int = 90):
    """Newest revision per cutoff; callers can request any revision separately."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('''SELECT DISTINCT ON (cutoff) * FROM revenue_results
            WHERE prime=%s AND cutoff BETWEEN %s AND %s
            ORDER BY cutoff DESC, publication_order DESC LIMIT %s''',
                    (prime, start, end, limit))
        return canonical(cur.fetchall())


def revisions(conn, prime: str, cutoff: date, *, limit: int = 100):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('''SELECT revision_id, computed_at, code_version, configuration_version,
            input_revision, result_hash FROM revenue_results WHERE prime=%s AND cutoff=%s
            ORDER BY publication_order DESC LIMIT %s''', (prime, cutoff, limit))
        return canonical(cur.fetchall())
