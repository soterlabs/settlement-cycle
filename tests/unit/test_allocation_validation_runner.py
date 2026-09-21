"""The diagnostic runner preserves pins and failures across retries."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from settle.domain.primes import Chain


def test_restart_reuses_pins_after_accounting_failure(monkeypatch, tmp_path):
    path = Path(__file__).parents[2] / 'scripts/validate_allocation_capital.py'
    spec = importlib.util.spec_from_file_location('allocation_validation_runner', path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused')
    monkeypatch.setenv('SETTLE_REQUIRE_POSTGRES', '0')
    monkeypatch.delenv('HYPERSYNC_NO_STORE', raising=False)
    monkeypatch.setattr('sys.argv', [str(path), 'spark', '--start', '2026-08-01',
                                  '--end', '2026-08-31', '--output-dir', str(tmp_path)])
    monkeypatch.setattr(runner, 'load_prime_by_id', lambda _: SimpleNamespace(chains=[Chain.ETHEREUM]))
    resolutions = []

    class Resolver:
        def block_at_or_before(self, chain, cutoff):
            resolutions.append((chain, cutoff))
            return 25878704

    monkeypatch.setattr(runner, 'HyperSyncBlockResolver', Resolver)

    def fail(*args, **kwargs):
        raise ValueError('accounting mismatch')

    monkeypatch.setattr(runner, 'fetch_capital_history', fail)
    with pytest.raises(ValueError, match='accounting mismatch'):
        runner.main()
    assert json.loads((tmp_path / 'status.json').read_text())['status'] == 'failed'
    assert not (tmp_path / 'result.json').exists()

    def history(prime, pins, **kwargs):
        assert pins == {Chain.ETHEREUM: 25878704}
        return SimpleNamespace(batches=[1], unsupported={})

    monkeypatch.setattr(runner, 'fetch_capital_history', history)
    monkeypatch.setattr(runner, 'replay_history', lambda *args:
                        SimpleNamespace(unmatched_receipts=[], unmatched_outflows=[]))
    runner.main()
    assert len(resolutions) == 1
    assert json.loads((tmp_path / 'status.json').read_text())['status'] == 'completed'
    assert json.loads((tmp_path / 'result.json').read_text())['batches'] == 1
