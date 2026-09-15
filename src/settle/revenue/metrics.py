"""Wall-time accounting for input extraction, including parallel source reads."""
import sys
import threading
import time


def union_seconds(intervals):
    total, end = 0.0, 0.0
    for start, stop in sorted(intervals):
        total += max(0.0, stop - max(start, end))
        end = max(end, stop)
    return total


class ExtractionTimer:
    """Profile outer extraction/source calls without double-counting nesting.

    Only used by the measurement command, not the scheduled production worker.
    Includes source decoding and cache/database access; excludes the outer
    accounting arithmetic. Runtime includes profiling overhead.
    """
    def __init__(self):
        self.local = threading.local()
        self.intervals = []

    def profile(self, frame, event, arg):
        module = frame.f_globals.get('__name__', '')
        if not module.startswith(('settle.extract.', 'settle.normalize.sources.')):
            return
        depth = getattr(self.local, 'depth', 0)
        if event == 'call':
            if depth == 0:
                self.local.start = time.monotonic()
            self.local.depth = depth + 1
        elif event == 'return' and depth:
            self.local.depth = depth - 1
            if depth == 1:
                self.intervals.append((self.local.start, time.monotonic()))

    def __enter__(self):
        self.previous = sys.getprofile(), threading.getprofile()
        sys.setprofile(self.profile)
        threading.setprofile(self.profile)
        return self

    def __exit__(self, *args):
        sys.setprofile(self.previous[0])
        threading.setprofile(self.previous[1])

    @property
    def seconds(self):
        return union_seconds(self.intervals)
