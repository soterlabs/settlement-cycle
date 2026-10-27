from datetime import date, timedelta

import pytest

from settle.revenue.store import has_venue_views
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


def test_missing_only_backfill_preserves_existing_dates_including_yesterday():
    today = date(2026, 9, 23)
    first, last = date(2026, 9, 1), today - timedelta(days=1)
    published = {first + timedelta(days=i) for i in range(13, 22)}
    assert planned_dates(published, set(), today, first, last, missing_only=True) == [
        first + timedelta(days=i) for i in range(13)]
    assert planned_dates(published, set(), today, first, last) == [
        first + timedelta(days=i) for i in range(22)]
    assert planned_dates({first}, set(), today, first, first, missing_only=True) == []


def test_missing_only_requires_explicit_window():
    with pytest.raises(ValueError, match='requires an explicit'):
        planned_dates(set(), set(), date(2026, 9, 23), missing_only=True)


def test_view_backfill_resumes_only_missing_formats_newest_first():
    # For this mode the store supplies only cutoffs whose latest revision has
    # both views, not all dates with a legacy publication.
    dates = planned_dates({date(2026, 10, 24)}, set(), date(2026, 10, 27),
                          date(2026, 10, 23), date(2026, 10, 26),
                          missing_views_only=True, newest_first=True)
    assert dates == [date(2026, 10, 26), date(2026, 10, 25), date(2026, 10, 23)]


@pytest.mark.parametrize('kwargs', [dict(missing_views_only=True), dict(newest_first=True)])
def test_view_backfill_modes_require_explicit_window(kwargs):
    with pytest.raises(ValueError, match='requires an explicit'):
        planned_dates(set(), set(), date(2026, 10, 27), **kwargs)


def test_backfill_modes_are_mutually_exclusive():
    with pytest.raises(ValueError, match='mutually exclusive'):
        planned_dates(set(), set(), date(2026, 10, 27), date(2026, 10, 1), date(2026, 10, 26),
                      missing_only=True, missing_views_only=True)


@pytest.mark.parametrize('result,expected', [
    ({}, False), (None, False), ({'venue_views': None}, False),
    ({'venue_views': {'schema_version': '2.0', 'settlement': [], 'reporting': []}}, True),
    ({'venue_views': {'schema_version': '2.0', 'settlement': []}}, False),
    ({'venue_views': {'schema_version': '2.0', 'settlement': {}, 'reporting': []}}, False),
    ({'venue_views': {'schema_version': 'old', 'settlement': [], 'reporting': []}}, False),
])
def test_both_views_are_required_for_format_compatibility(result, expected):
    assert has_venue_views(result) is expected
