"""Real Postgres + fresh processes; upstream reads remain deterministic.

Set SETTLE_TEST_POSTGRES_URL to a disposable test server. Each test creates and
removes only its own randomly named schema, never production cache tables.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

_WORKER = r'''
import json, os
from datetime import date
from settle.extract import hypersync, hypersync_store, rpc
from settle.extract.input_cache import revenue_input_scope
from settle.domain.primes import Chain, Address
rpc_reads, log_reads = [], []
hypersync.archive_height = lambda chain: 10000

def post(url, method, args):
    rpc_reads.append(method)
    return '0x' + format(42, '064x')
rpc._post = post

def logs(chain, sel, lo, hi, **kwargs):
    log_reads.append([lo, hi])
    return hypersync.QueryResult(
        rows=[hypersync.LogRow(b, 0, 1700000000+b, '0xtoken', '0xtopic', None, None, None, '0x01')
              for b in range(lo, hi+1) if b % 2 == 0], archive_height=10000)
hypersync.query_logs = logs
if os.environ.get('CRASH_BEFORE_COVERAGE') == '1':
    def crash(*args):
        raise RuntimeError('simulated interruption after rows, before coverage')
    hypersync_store._add_range = crash

@revenue_input_scope
def run(*, as_of):
    token = Address(bytes(20))
    assert rpc.balance_of(Chain.ETHEREUM, token, token, 500) == 42
    lo, hi = int(os.environ['TEST_LO']), int(os.environ['TEST_HI'])
    rows = hypersync_store.fetch_logs('ethereum', [{'address': ['0xtoken']}], lo, hi)
    assert [r.block_number for r in rows] == [b for b in range(lo, hi+1) if b % 2 == 0]
run(as_of=date(2026, 8, 15))
assert len(rpc_reads) == int(os.environ['EXPECTED_RPC']), rpc_reads
assert log_reads == json.loads(os.environ['EXPECTED_LOG_RANGES']), log_reads
print('persistent input reuse verified')
'''


@pytest.fixture
def database(monkeypatch):
    url = os.environ.get("SETTLE_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("SETTLE_TEST_POSTGRES_URL is required for isolated Postgres integration")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    schema = "test_input_cache_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped = make_conninfo(url, options=f"-c search_path={schema}")
    try:
        with psycopg.connect(scoped, autocommit=True) as conn:
            conn.execute(Path("db/schema.sql").read_text())
        yield scoped
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def worker(database, tmp_path, lo, hi, expected_rpc, ranges, *, crash=False):
    import json
    env = dict(os.environ, DATABASE_URL=database, SETTLE_REQUIRE_POSTGRES="1",
               SETTLE_CACHE_DIR=str(tmp_path / uuid.uuid4().hex),
               PYTHON_DOTENV_DISABLED="1", ETH_RPC="http://unused.invalid",
               TEST_LO=str(lo), TEST_HI=str(hi), EXPECTED_RPC=str(expected_rpc),
               EXPECTED_LOG_RANGES=json.dumps(ranges), CRASH_BEFORE_COVERAGE=str(int(crash)),
               SETTLE_NO_CACHE="0", HYPERSYNC_NO_STORE="0", HYPERSYNC_REORG_MARGIN="500",
               SETTLE_INPUT_REVISION="0")
    result = subprocess.run([sys.executable, "-c", _WORKER], env=env,
                            capture_output=True, text=True, timeout=30)
    if crash:
        assert result.returncode != 0
        assert "simulated interruption" in result.stderr
    else:
        assert result.returncode == 0, result.stderr


def test_postgres_survives_worker_restarts_and_fetches_only_missing_ranges(database, tmp_path):
    worker(database, tmp_path, 0, 10, 1, [[0, 10]])
    worker(database, tmp_path, 0, 10, 0, [])
    worker(database, tmp_path, 0, 20, 0, [[11, 20]])
    worker(database, tmp_path, 100, 110, 0, [[100, 110]])
    worker(database, tmp_path, 100, 110, 0, [])
    worker(database, tmp_path, 5, 105, 0, [[21, 99]])
    worker(database, tmp_path, 0, 110, 0, [])


def test_interrupted_write_does_not_claim_coverage(database, tmp_path):
    worker(database, tmp_path, 0, 10, 1, [[0, 10]], crash=True)
    worker(database, tmp_path, 0, 10, 0, [[0, 10]])
    worker(database, tmp_path, 0, 10, 0, [])


def test_legacy_coverage_migrates_before_disjoint_backfill(database, tmp_path):
    import psycopg
    worker(database, tmp_path, 0, 10, 1, [[0, 10]])
    # Recreate the pre-migration schema state: persisted rows and one legacy
    # coverage claim, but no claims in the newly introduced interval table.
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute("DELETE FROM hypersync_ranges")
    worker(database, tmp_path, 100, 200, 0, [[100, 200]])
    worker(database, tmp_path, 0, 10, 0, [])
    worker(database, tmp_path, 0, 200, 0, [[11, 99]])


def test_required_database_miss_never_imports_a_local_only_value(database, tmp_path):
    """A shared local directory cannot repopulate a cleared/different input DB."""
    import psycopg
    program = '''
import os
from settle.extract.cache import cached
@cached('acceptance.local_only')
def read(): return int(os.environ['UPSTREAM_VALUE'])
assert read() == int(os.environ['EXPECTED_VALUE'])
'''
    def run(upstream, expected):
        env = dict(os.environ, PYTHON_DOTENV_DISABLED='1', DATABASE_URL=database,
                   SETTLE_REQUIRE_POSTGRES='1', SETTLE_NO_CACHE='0',
                   SETTLE_CACHE_DIR=str(tmp_path / 'shared'),
                   UPSTREAM_VALUE=str(upstream), EXPECTED_VALUE=str(expected))
        result = subprocess.run([sys.executable, '-c', program], env=env,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
    run(41, 41)
    with psycopg.connect(database, autocommit=True) as conn:
        conn.execute("DELETE FROM raw_data WHERE source='acceptance.local_only'")
    run(42, 42)
    run(99, 42)  # The new database-backed value survives another restart.
