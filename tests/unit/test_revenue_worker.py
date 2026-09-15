from datetime import date, timedelta

import pytest

from settle.revenue.worker import planned_dates


def test_first_install_and_month_boundary_target_previous_utc_day():
    assert planned_dates(set(), set(), date(2026, 9, 1)) == [date(2026, 8, 31)]


def test_gaps_are_retried_even_after_a_later_success():
    today = date(2026, 9, 15)
    assert planned_dates({today-timedelta(days=4), today-timedelta(days=2)}, set(), today) == [
        today-timedelta(days=3), today-timedelta(days=1)]


def test_failed_first_install_is_caught_up_and_old_history_is_bounded():
    today = date(2026, 9, 15)
    dates = planned_dates(set(), {today-timedelta(days=2)}, today)
    assert dates == [today-timedelta(days=2), today-timedelta(days=1)]
    assert planned_dates({today-timedelta(days=100)}, set(), today) == [today-timedelta(days=1)]
    with pytest.raises(ValueError):
        planned_dates(set(), set(), today, today-timedelta(days=91), today-timedelta(days=1))


def test_deployment_start_recovers_missing_first_tick_and_stays_bounded():
    today = date(2026, 9, 15)
    initial = today - timedelta(days=3)
    assert planned_dates(set(), set(), today, initial=initial) == [
        initial, initial + timedelta(days=1), initial + timedelta(days=2)]
    assert len(planned_dates(set(), set(), today, initial=today-timedelta(days=200))) == 90


def test_hard_deadline_terminates_instead_of_being_swallowed():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-c',
        'import time; from settle.revenue.worker import deadline\n'
        'with deadline(.05):\n'
        ' try: time.sleep(10)\n'
        ' except Exception: pass\n'], capture_output=True, timeout=10)
    assert result.returncode == 124
    assert b'deadline exceeded' in result.stderr
