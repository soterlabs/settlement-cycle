"""Complete configured-prime calculations; real Postgres, deterministic transport.

This deliberately synthetic zero-position scenario exercises source wiring and
fresh-process persistence; it is not evidence of live financial parity.
"""
import json
import os
import subprocess
import sys

import pytest

from settle.revenue.verification import PRIMES, compare
from tests.integration.test_input_cache_postgres import database  # noqa: F401

_PROGRAM = '''
import json, os, requests
from datetime import date
from unittest.mock import patch
from tests.fixtures.revenue_transport import send
from settle.extract.rpc import RPC_ENV_VARS
from settle.revenue.verification import calculate
for name in RPC_ENV_VARS.values(): os.environ[name] = 'http://fixture.invalid'
with patch.object(requests.Session, 'send', send):
 result = calculate(os.environ['TEST_PRIME'], date(2026, 8, 1))
open(os.environ['TEST_REPORT'], 'w').write(json.dumps(result))
'''


@pytest.mark.parametrize("prime", PRIMES)
def test_complete_same_date_calculation_reuses_postgres_after_restart(database, tmp_path, prime):  # noqa: F811
    reports = []
    for attempt in range(2):
        output = tmp_path / f"{attempt}.json"
        env = dict(os.environ, PYTHON_DOTENV_DISABLED="1", DATABASE_URL=database,
                   SETTLE_REQUIRE_POSTGRES="1", SETTLE_NO_CACHE="0", HYPERSYNC_NO_STORE="0",
                   SETTLE_INPUT_REVISION="0", ENVIO_API_TOKEN="fixture",
                   SETTLE_CACHE_DIR=str(tmp_path / f"cache-{attempt}"),
                   TEST_PRIME=prime, TEST_REPORT=str(output))
        process = subprocess.run([sys.executable, '-c', _PROGRAM], env=env,
                                 capture_output=True, text=True, timeout=90)
        assert process.returncode == 0, process.stderr
        reports.append(json.loads(output.read_text()))
    verdict = compare(*reports)
    assert verdict['passed'], verdict
