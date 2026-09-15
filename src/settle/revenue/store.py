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
        subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', 'src', 'config'], cwd=_ROOT, check=True)
        code = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=_ROOT, text=True).strip()
    files = sorted((_ROOT / 'config').rglob('*.yaml'))
    configuration = digest({str(p.relative_to(_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in files})
    return Versions(code, configuration, os.environ.get('SETTLE_INPUT_REVISION', '0'))


def apply_schema(conn):
    conn.execute((_ROOT / 'db' / 'schema_revenue.sql').read_text())


def publish(conn, pnl, versions: Versions):
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
             configuration_version, input_revision, result, result_hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING''',
            (revision, pnl.prime_id, pnl.as_of, Jsonb(opening), Jsonb(closing), versions.code,
             versions.configuration, versions.inputs, Jsonb(payload), result_hash))
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
                    + ' ORDER BY cutoff DESC, computed_at DESC, revision_id DESC LIMIT 1', params)
        row = cur.fetchone()
    return canonical(row) if row else None


def history(conn, prime: str, *, start: date, end: date, limit: int = 90):
    """Newest revision per cutoff; callers can request any revision separately."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('''SELECT DISTINCT ON (cutoff) * FROM revenue_results
            WHERE prime=%s AND cutoff BETWEEN %s AND %s
            ORDER BY cutoff DESC, computed_at DESC, revision_id DESC LIMIT %s''',
                    (prime, start, end, limit))
        return canonical(cur.fetchall())


def revisions(conn, prime: str, cutoff: date, *, limit: int = 100):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('''SELECT revision_id, computed_at, code_version, configuration_version,
            input_revision, result_hash FROM revenue_results WHERE prime=%s AND cutoff=%s
            ORDER BY computed_at DESC, revision_id DESC LIMIT %s''', (prime, cutoff, limit))
        return canonical(cur.fetchall())
