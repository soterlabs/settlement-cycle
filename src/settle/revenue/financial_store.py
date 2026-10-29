"""Persist/replay derived financial metrics without provider calls or source writes."""
from __future__ import annotations

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .financial import POLICY_VERSION, SUPPORTED_POLICIES, build
from .verification import canonical, digest


def publish(conn, source):
    document = build(source)
    revision = digest({'source_revision': source['revision_id'], 'source_hash': source['result_hash'],
                       'policy_version': POLICY_VERSION})
    hashed = digest(document)
    conn.execute('''INSERT INTO reporting_financial_results
        (revision_id,source_revision,source_order,prime,cutoff,policy_version,document,document_hash)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
        (revision, source['revision_id'], source['publication_order'], source['prime'], source['cutoff'],
         POLICY_VERSION, Jsonb(document), hashed))
    saved = conn.execute('SELECT document_hash FROM reporting_financial_results WHERE revision_id=%s', (revision,)).fetchone()
    if saved[0] != hashed:
        raise ValueError('Financial policy changed without a new version')
    attempt(conn, source, 'succeeded')
    return revision


def attempt(conn, source, status, error_type=None):
    conn.execute('''INSERT INTO reporting_financial_attempts
        (source_revision,policy_version,prime,status,error_type) VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (source_revision,policy_version) DO UPDATE SET
        attempted_at=NOW(),status=EXCLUDED.status,error_type=EXCLUDED.error_type''',
        (source['revision_id'], POLICY_VERSION, source['prime'], status, error_type))


def read(conn, prime, *, cutoff=None, source_revision=None, revision=None):
    clauses, params = ['prime=%s', 'policy_version = ANY(%s)'], [prime, list(SUPPORTED_POLICIES) if revision else [POLICY_VERSION]]
    for field, value in (('cutoff', cutoff), ('source_revision', source_revision), ('revision_id', revision)):
        if value is not None:
            clauses.append(field + '=%s')
            params.append(value)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT * FROM reporting_financial_results WHERE ' + ' AND '.join(clauses)
                    + ' ORDER BY cutoff DESC,source_order DESC,publication_order DESC LIMIT 1', params)
        row = cur.fetchone()
    return canonical(row) if row else None


def year_rows(conn, prime, year, *, revisions=None, policy_version=POLICY_VERSION, include_rwa=False):
    if policy_version not in SUPPORTED_POLICIES:
        raise ValueError("Unknown financial policy")
    with conn.cursor(row_factory=dict_row) as cur:
        if revisions is not None:
            cur.execute('''SELECT f.*, EXISTS(SELECT 1 FROM revenue_results r
                WHERE r.prime=f.prime AND r.cutoff=f.cutoff AND r.publication_order>f.source_order) AS source_lag
                FROM reporting_financial_results f WHERE prime=%s AND policy_version=%s
                AND revision_id = ANY(%s)''', (prime, policy_version, revisions))
        else:
            cur.execute('''SELECT DISTINCT ON (f.cutoff) f.*, EXISTS(SELECT 1 FROM revenue_results r
                WHERE r.prime=f.prime AND r.cutoff=f.cutoff AND r.publication_order>f.source_order) AS source_lag
                FROM reporting_financial_results f
                WHERE f.prime=%s AND f.policy_version=%s AND f.cutoff >= %s AND f.cutoff <= %s
                ORDER BY f.cutoff,f.source_order DESC,f.publication_order DESC''',
                (prime, policy_version, f'{year}-01-01', f'{year}-12-31'))
        rows = cur.fetchall()
        if include_rwa and prime == 'grove' and rows:
            # One extra query in the same repeatable-read transaction. Extract
            # only the two reviewed rows, not entire daily result documents.
            cur.execute('''SELECT revision_id,result_hash,
                jsonb_path_query_array(result, '$.venue_breakdown[*] ? (@.venue_id == "E7" || @.venue_id == "E8")') AS rows,
                jsonb_path_query_array(result, '$.venue_views.reporting[*] ? (@.venue_id == "E7" || @.venue_id == "E8")') AS views
                FROM revenue_results WHERE prime=%s AND revision_id = ANY(%s)''',
                (prime, [row['source_revision'] for row in rows]))
            sources = {source['revision_id']: source for source in cur.fetchall()}
            for row in rows:
                source = sources.get(row['source_revision'])
                if source:
                    row.update(rwa_source_hash=source['result_hash'], rwa_source_rows=source['rows'],
                               rwa_source_views=source['views'])
        return canonical(rows)


def publish_revision(conn, prime, revision):
    from . import store
    source = store.read(conn, prime, revision=revision)
    try:
        with conn.transaction():
            publish(conn, source)
        return 'succeeded'
    except Exception as exc:
        with conn.transaction():
            attempt(conn, source, 'failed', type(exc).__name__)
        return 'failed'
