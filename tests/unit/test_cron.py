"""Unit tests for the scheduled entry point's mutual exclusion."""

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
    def __init__(self, row, log, rowcount=0):
        self._row, self._log, self.rowcount = row, log, rowcount

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=()):
        self._log.append((" ".join(sql.split()), params))

    def fetchone(self): return self._row


class _Conn:
    def __init__(self, row=None, rowcount=0):
        self.row, self.log, self.rowcount = row, [], rowcount

    def cursor(self): return _Cur(self.row, self.log, self.rowcount)


def test_lock_key_is_stable_and_per_kind_and_fits_bigint():
    a = cron.lock_key("tmf_history")
    assert a == cron.lock_key("tmf_history")          # stable across calls
    assert a != cron.lock_key("msc_mtd")              # phase 2 gets its own lock
    assert -(2**63) <= a < 2**63                      # pg_try_advisory_lock(bigint)


def test_try_lock_uses_pg_try_advisory_lock_and_reports_the_answer():
    taken = _Conn((True,))
    assert cron.try_lock(taken, "tmf_history") is True
    sql, params = taken.log[0]
    assert sql == "SELECT pg_try_advisory_lock(%s)"
    assert params == (cron.lock_key("tmf_history"),)
    assert cron.try_lock(_Conn((False,)), "tmf_history") is False
    assert cron.try_lock(_Conn(None), "tmf_history") is False


def test_reclaim_marks_only_runs_older_than_the_window():
    conn = _Conn(rowcount=2)
    assert cron.reclaim_abandoned(conn, "tmf_history", 3) == 2
    sql, params = conn.log[0]
    assert "SET status = 'failed'" in sql
    assert "status = 'running'" in sql
    assert "started_at < NOW() - make_interval(hours => %s)" in sql
    assert params == (3, "tmf_history", 3)


def test_abandon_window_is_generous_relative_to_the_hourly_tick():
    """The lock — not this window — is what prevents overlap, so the window can
    be well beyond one tick: it only decides when a DEAD run's row is tidied up,
    and marking a live-but-slow run as failed would be a lie."""
    assert cron._ABANDONED_AFTER_HOURS >= 2
