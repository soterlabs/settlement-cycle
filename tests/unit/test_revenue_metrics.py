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
