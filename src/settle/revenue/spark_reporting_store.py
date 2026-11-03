"""Append-only reporting components, isolated from MSC and native revenue."""
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .financial import number
from .verification import canonical, digest


def validate_document(document):
    if document.get('schema_version') != 'spark-onchain.v1' or document['period_start'] != document['cutoff'][:7] + '-01':
        raise ValueError('Invalid Spark component identity')
    date.fromisoformat(document['cutoff'])
    with localcontext() as ctx:
        ctx.prec = 60
        for row in document.get('curator_fees', {}).values():
            if row.get('status') in ('available', 'partial'):
                opening, earned, paid, closing = (number(row[key]) for key in
                    ('opening_pending_usd', 'accrued_usd', 'received_usd', 'closing_pending_usd'))
                if min(opening, paid, closing) < 0 or opening + earned - paid != closing:
                    raise ValueError('Unreconciled curator fee ledger')
        for row in document.get('gains', {}).values():
            if row.get('status') in ('available', 'partial'):
                net = number(row['value'])
                if 'asset_price_gain_usd' in row and abs(number(row['asset_price_gain_usd']) - number(row['depositor_offset_usd']) - net) > Decimal('1e-36'):
                    raise ValueError('Net gain does not offset depositor liabilities')


def publish(conn, document, *, code_version, configuration_version):
    if (document.get('schema_version') != 'spark-onchain.v1'
            or document['period_start'] != document['cutoff'][:7] + '-01'):
        raise ValueError('Invalid Spark reporting component period')
    validate_document(document)
    if not code_version or not configuration_version:
        raise ValueError('Missing Spark reporting component version')
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('''SELECT * FROM reporting_spark_components WHERE cutoff BETWEEN %s AND %s
            ORDER BY cutoff DESC,publication_order DESC LIMIT 1''', (document['period_start'], document['cutoff']))
        previous = cur.fetchone()
    if previous:
        document = carry_forward(document, previous)
    validate_document(document)
    identity = {'document_hash': digest(document), 'code_version': code_version,
                'configuration_version': configuration_version}
    revision = digest(identity)
    conn.execute('''INSERT INTO reporting_spark_components
        (revision_id,cutoff,document,document_hash,code_version,configuration_version)
        VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
        (revision, document['cutoff'], Jsonb(document), identity['document_hash'], code_version, configuration_version))
    return revision


def carry_forward(document, previous):
    """Retain known same-month amounts after a failed refresh, with old bounds."""
    result = deepcopy(document)
    if previous['document']['period_start'] != document['period_start']:
        return result
    def retained(new, old):
        if new.get('status') != 'unavailable' or old.get('status') not in ('available', 'partial'):
            return new
        return {**old, 'status': 'partial', 'reason': 'retained_after_refresh_failure',
                'period_end': old.get('period_end', str(previous['cutoff'])),
                'retained_source_revision': old.get('retained_source_revision', previous['revision_id'])}
    result['reserve_fees'] = retained(result.get('reserve_fees', {}), previous['document'].get('reserve_fees', {}))
    for family in ('curator_fees', 'gains', 'native_supplies'):
        for key, value in result.get(family, {}).items():
            result[family][key] = retained(value, previous['document'].get(family, {}).get(key, {}))
    return result


def year_rows(conn, year, *, revisions=None):
    with conn.cursor(row_factory=dict_row) as cur:
        if revisions is not None:
            cur.execute('SELECT * FROM reporting_spark_components WHERE revision_id = ANY(%s)', (revisions,))
        else:
            cur.execute('''SELECT DISTINCT ON (cutoff) * FROM reporting_spark_components
                WHERE cutoff BETWEEN %s AND %s ORDER BY cutoff,publication_order DESC''',
                (f'{year}-01-01', f'{year}-12-31'))
        rows = canonical(cur.fetchall())
    for row in rows:
        if digest(row['document']) != row['document_hash']:
            raise ValueError('Spark reporting component hash mismatch')
        validate_document(row['document'])
        row['source'] = {'kind': 'spark_onchain_components', 'revision_id': row['revision_id'],
                         'cutoff': row['cutoff'], 'result_hash': row['document_hash'], 'currency': 'USD',
                         'code_version': row['code_version'], 'configuration_version': row['configuration_version'],
                         'opening_pins': row['document'].get('opening_pins', {}), 'closing_pins': row['document'].get('closing_pins', {})}
    return rows


def select(rows, year, as_of, selection=None):
    chosen = []
    for month in range(1, 13):
        key = f'{year}-{month:02}'
        if selection is not None:
            ref = selection['offchain_revisions'][month - 1]
            match = next((row for row in rows if row['revision_id'] == ref), None) if ref else None
            if ref and (not match or match['cutoff'][:7] != key or match['cutoff'] > str(as_of)):
                raise ValueError('Retained Spark component revision unavailable')
        else:
            matches = [row for row in rows if row['cutoff'][:7] == key and row['cutoff'] <= str(as_of)]
            match = max(matches, key=lambda row: (row['cutoff'], row['publication_order']), default=None)
        chosen.append(match)
    return chosen
