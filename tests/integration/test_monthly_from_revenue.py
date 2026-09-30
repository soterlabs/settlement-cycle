from dataclasses import replace

import psycopg

from settle.load import writer
from settle.revenue import monthly, store
from settle.revenue.verification import canonical, validate_window
from tests.integration.test_input_cache_postgres import database  # noqa: F401
from tests.unit.test_monthly_from_revenue import MONTH, TODAY, VERSIONS, example


def test_persisted_revision_to_monthly_artifacts(database, tmp_path, monkeypatch):  # noqa: F811
    prime, pnl, record = example(subsidy=True)
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
    # Exercise actual serialization and XLSX subprocess in the isolated directory.
    monkeypatch.setattr(writer, 'enrich_with_dr', lambda p: p)
    written = writer.write_settlement(result, tmp_path / 'review', sources=sources)
    assert set(written) == {'provenance', 'summary', 'xlsx'}
    assert all(p.parent == tmp_path / 'review' for p in written.values())
    assert all(p.exists() for p in written.values())
