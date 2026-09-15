import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from settle.extract import rpc
from settle.revenue.metrics import ExtractionTimer, union_seconds


def test_overlapping_extraction_intervals_are_not_double_counted():
    assert union_seconds([(1, 5), (2, 3), (4, 7), (10, 12)]) == 8
    assert union_seconds([]) == 0


def test_timer_observes_new_worker_threads_and_restores_profile():
    import sys
    previous = sys.getprofile()
    with patch.object(rpc, '_post', lambda *a: time.sleep(.01) or '0x1'), patch.object(rpc, 'rpc_url', lambda *a: 'unused'):
        with ExtractionTimer() as timer:
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda _: rpc.native_balance.__wrapped__(None, type('A', (), {'hex': '0x0'})(), 1), range(2)))
    assert timer.seconds >= .01
    assert sys.getprofile() is previous


def test_measurement_window_is_validated_before_starting_workers():
    from datetime import date

    import pytest

    from settle.revenue.verification import validate_window
    today = date(2026, 9, 15)
    validate_window(date(2026, 6, 17), today=today)
    validate_window(date(2026, 9, 13), advance=True, today=today)
    for cutoff, advance in [(date(2026, 6, 16), False), (today, False), (date(2026, 9, 14), True)]:
        with pytest.raises(ValueError):
            validate_window(cutoff, advance, today)


def test_advance_is_not_measured_after_failed_same_date_gate(monkeypatch, tmp_path):
    from datetime import UTC, datetime, timedelta

    import pytest

    from settle.revenue import verification as mod
    cutoff = datetime.now(UTC).date() - timedelta(days=2)
    monkeypatch.setenv('DATABASE_URL', 'test')
    monkeypatch.setattr('sys.argv', ['verify', '--prime', 'obex', '--as-of', str(cutoff),
                                   '--advance', '--output', str(tmp_path)])
    calls = []
    def worker(*args):
        calls.append(args)
        return dict(prime='obex', cutoff=str(cutoff), result=len(calls), calls=[],
                    elapsed_seconds=1, extraction_seconds=.5, calculation_seconds=.5,
                    cache={}, dune_attempts=0)
    monkeypatch.setattr(mod, 'worker', worker)
    with pytest.raises(SystemExit, match='verification failed'):
        mod.main()
    assert len(calls) == 2
