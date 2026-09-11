"""Unit tests for the scheduled entry point's overlap guard."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))
_spec = importlib.util.spec_from_file_location("settle_cron", _REPO / "scripts" / "cron.py")
assert _spec and _spec.loader
cron = importlib.util.module_from_spec(_spec)


@pytest.fixture(scope="module", autouse=True)
def _load():
    pytest.importorskip("yaml")
    _spec.loader.exec_module(cron)


class _Cur:
    def __init__(self, row, log):
        self._row, self._log = row, log

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=()):
        self._log.append((" ".join(sql.split()), params))

    def fetchone(self): return self._row


class _Conn:
    def __init__(self, row=None):
        self.row, self.log = row, []

    def cursor(self): return _Cur(self.row, self.log)


def test_no_blocking_run_when_none_is_running():
    conn = _Conn(None)
    assert cron._blocking_run(conn, "tmf_history", 50) is None
    sql, params = conn.log[0]
    assert "status = 'running'" in sql and params == ("tmf_history", 50)
    assert "make_interval(mins => %s)" in sql


def test_blocking_run_is_reported_with_its_start():
    conn = _Conn((7, "2026-09-11T08:00:00Z"))
    assert cron._blocking_run(conn, "tmf_history", 50) == (7, "2026-09-11T08:00:00Z")


def test_overlap_window_is_under_the_hourly_period():
    """The guard must expire before the next-but-one tick, so a crashed run
    cannot block the schedule indefinitely."""
    assert 0 < cron._OVERLAP_WINDOW_MIN < 60
