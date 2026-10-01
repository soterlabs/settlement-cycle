import json
from dataclasses import replace

import psycopg

from settle import cli
from settle.load import writer
from settle.revenue import monthly, store
from settle.revenue.verification import canonical, validate_window
from tests.integration.test_input_cache_postgres import database  # noqa: F401
from tests.unit.test_monthly_from_revenue import MONTH, TODAY, VERSIONS, example


def test_persisted_revision_to_monthly_artifacts(database, tmp_path, monkeypatch):  # noqa: F811
    prime, pnl, record = example(subsidy=True, basin=True)
    # Freeze the clock just after this fixture month closes; retain the real guard.
    monkeypatch.setattr(store, 'validate_window', lambda d: validate_window(d, today=TODAY))
    monkeypatch.setattr(store, 'capture_versions', lambda: VERSIONS)
    original_finalize = monthly.finalize
    monkeypatch.setattr(monthly, 'finalize',
                        lambda *a: original_finalize(*a, today=TODAY))
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        first = store.publish(conn, pnl, replace(VERSIONS, inputs='resolved'),
                              input_provenance=record['input_provenance'])
        # A later published revision must not replace the explicitly selected one.
        store.publish(conn, pnl, replace(VERSIONS, inputs='later'),
                      input_provenance=record['input_provenance'])
        with conn.transaction():
            conn.execute('SET TRANSACTION READ ONLY')
            result, sources = monthly.from_database(conn, prime, MONTH, first)
        assert canonical(result) == canonical(pnl)
        assert sources['daily_revenue_revision'] == first
        assert conn.execute('SELECT count(*) FROM revenue_results').fetchone()[0] == 2
    # Exercise the actual CLI connection, read-only transaction, serialization
    # and XLSX subprocess in the isolated directory.
    monkeypatch.setattr(writer, 'enrich_with_dr', lambda p: p)
    monkeypatch.setattr(cli, 'load_prime_by_id', lambda _: prime)
    monkeypatch.setenv('DATABASE_URL', database)
    output = tmp_path / 'review'
    assert cli.main(['monthly-from-revenue', '--prime', prime.id, '--month', str(MONTH),
                     '--revision', first, '--output-dir', str(output)]) == 0
    assert {p.name for p in output.iterdir()} == {
        'provenance.json', 'summary.md', 'grove_settlement_september_2026.xlsx'}
    final = json.loads((output / 'provenance.json').read_text())
    assert final['sources']['daily_revenue_revision'] == first
    assert final['results']['sky_revenue'] == str(pnl.sky_revenue)
    with psycopg.connect(database) as conn:
        assert conn.execute('SELECT count(*) FROM revenue_results').fetchone()[0] == 2

    from openpyxl import load_workbook
    book = load_workbook(output / 'grove_settlement_september_2026.xlsx')
    debt_rows = list(book['Debt'].values)
    header = next(r for r in debt_rows if r[0] == 'Date')
    basin_column = header.index('- Basin idle')
    first_day = next(r for r in debt_rows if r[0] == '2026-09-01')
    assert first_day[basin_column] == 10000000
