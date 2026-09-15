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
