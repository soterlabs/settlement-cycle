"""The linear event sweep must reproduce the old fee calculation exactly."""
from decimal import Decimal
from random import Random

from settle.normalize.sources.hypersync_non_msc import _integrate_fee


def reference(arts, folds, duties, start, end):
    """Direct checkpoint definition, independent of the optimized sweep."""
    def state(key, timestamp):
        debt = sum(a[3] for a in arts if a[:2] <= key)
        accrued = [f for f in folds if f[:2] <= key]
        rate = 10**27 + sum(f[3] for f in accrued)
        rho = accrued[-1][2]
        duty = [d[3] for d in duties if d[:2] <= key][-1]
        return debt, float(rate) * ((duty / 1e27)**float(timestamp - rho))
    checkpoints = [((9, 99), start)]
    checkpoints += [(a[:2], a[2]) for a in arts if start <= a[2] < end]
    checkpoints += [((10**9, 99), end)]
    states = [state(key, t) for key, t in checkpoints]
    total = sum((a[0] / 1e18) * (b[1] - a[1])
                for a, b in zip(states, states[1:]))
    return Decimal(str(total / 1e27))


def test_event_sweep_matches_checkpoint_definition_with_interleaved_events():
    rng = Random(202609)
    for _ in range(100):
        arts = [(1, 0, 900, 10**26)]
        folds = [(2, 0, 910, 10**25)]
        duties = [(3, 0, 920, 10**27 + 10**18)]
        for block in range(10, 60):
            # Several changes per block expose event-ordering errors that
            # date-only comparisons would miss. Include repayments too.
            for log_index in range(4):
                stream = rng.choice([arts, folds, duties])
                if stream is arts:
                    value = rng.randint(-10, 20) * 10**18
                elif stream is folds:
                    value = rng.randint(0, 20) * 10**20
                else:
                    value = 10**27 + rng.randint(1, 20) * 10**18
                stream.append((block, log_index, 1000 + (block - 10) * 12, value))
        assert _integrate_fee(arts, folds, duties, 1000, 1600) == reference(
            arts, folds, duties, 1000, 1600)
