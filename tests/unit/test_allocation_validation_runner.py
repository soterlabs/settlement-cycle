"""The diagnostic runner preserves pins and failures across retries."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from settle.domain.primes import Chain
from settle.normalize.allocation_capital import CapitalHistory


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
        return CapitalHistory((), {}, {})

    monkeypatch.setattr(runner, 'fetch_capital_history', history)
    monkeypatch.setattr(runner, 'replay_history', lambda *args:
                        SimpleNamespace(unmatched_receipts=[], unmatched_outflows=[]))
    runner.main()
    assert len(resolutions) == 1
    assert json.loads((tmp_path / 'status.json').read_text())['status'] == 'completed'
    assert json.loads((tmp_path / 'result.json').read_text())['batches'] == 0

    # Normalization succeeded but a replay fails: inputs survive and retry
    # must not execute the fetcher again.
    monkeypatch.setattr(runner, 'fetch_capital_history', fail)
    monkeypatch.setattr(runner, 'replay_history', fail)
    with pytest.raises(ValueError, match='accounting mismatch'):
        runner.main()
    assert (tmp_path / 'history.jsonl.gz').exists()
    monkeypatch.setattr(runner, 'replay_history', lambda *args:
                        SimpleNamespace(unmatched_receipts=[], unmatched_outflows=[]))
    runner.main()
    assert json.loads((tmp_path / 'status.json').read_text())['status'] == 'completed'


def test_capture_uses_settlement_pins_without_origin_replay(monkeypatch, tmp_path):
    from settle.domain.config import load_prime_by_id

    path = Path(__file__).parents[2] / 'scripts/validate_allocation_capital.py'
    spec = importlib.util.spec_from_file_location('allocation_validation_capture', path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    control = tmp_path / 'provenance.json'
    control.write_text(json.dumps({'prime_id': 'spark', 'period': {
        'start': '2026-09-01', 'end': '2026-09-30'}, 'pin_blocks_eom': {'ethereum': 123}}))
    output = tmp_path / 'history'
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused')
    monkeypatch.setenv('SETTLE_REQUIRE_POSTGRES', '0')
    monkeypatch.delenv('HYPERSYNC_NO_STORE', raising=False)
    monkeypatch.setattr('sys.argv', [str(path), 'spark', '--start', '2026-09-01',
        '--end', '2026-09-30', '--output-dir', str(output), '--provenance', str(control),
        '--capture-only'])
    monkeypatch.setattr(runner, 'fingerprint', lambda *args: 'test-fingerprint')

    def fetch(prime, pins, **kwargs):
        assert all(v.chain == Chain.ETHEREUM for v in prime.venues)
        assert pins == {Chain.ETHEREUM: 123}
        return CapitalHistory((), {}, {})

    def forbidden(*args, **kwargs):
        pytest.fail('Capture must not resolve fresh pins or replay funding origins')

    monkeypatch.setattr(runner, 'fetch_capital_history', fetch)
    monkeypatch.setattr(runner, 'replay_history', forbidden)
    monkeypatch.setattr(runner, 'HyperSyncBlockResolver', lambda: SimpleNamespace(block_at_or_before=forbidden))
    runner.main()
    manifest = json.loads((output / 'pins.json').read_text())
    assert manifest['pins'] == {'ethereum': 123}
    result = json.loads((output / 'result.json').read_text())
    assert result['normalized_only'] is True
    omitted = {v.id for v in load_prime_by_id('spark').venues if v.chain != Chain.ETHEREUM}
    assert set(result['unsupported']) == omitted
    control.write_text(control.read_text().replace('123', '124'))
    with pytest.raises(SystemExit):
        runner.main()
