"""Historical reruns cannot silently replace saved settlement artifacts."""
import importlib.util
import json
import sys
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from settle import cli
from settle.domain.period import Period
from settle.load import writer
from tests.unit.test_monthly_from_revenue import example

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def pnl(monkeypatch):
    _, result, _ = example()
    monkeypatch.setattr(writer, 'enrich_with_dr', lambda result: result)
    monkeypatch.setattr(writer, '_build_canonical_xlsx', lambda *args: None)
    return result


@pytest.mark.parametrize('name', [
    'summary.md', 'provenance.json', 'grove_settlement_september_2026.xlsx',
])
def test_existing_artifact_blocks_all_writes_before_enrichment(tmp_path, pnl, monkeypatch, name):
    path = tmp_path / name
    original = b'original published bytes\x00\xff'
    path.write_bytes(original)

    def forbidden(*args, **kwargs):
        pytest.fail('Blocked historical rerun reached enrichment or a writer')

    monkeypatch.setattr(writer, 'enrich_with_dr', forbidden)
    monkeypatch.setattr(writer, 'write_provenance', forbidden)
    with pytest.raises(FileExistsError, match='separate output directory'):
        writer.write_settlement(pnl, tmp_path)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == {name: original}


def test_reconciliation_output_keeps_original_report_intact(tmp_path, pnl):
    original = tmp_path / 'published'
    original.mkdir()
    (original / 'summary.md').write_text('published with historical rate rules')
    alternate = tmp_path / 'reconciliation'
    changed = replace(pnl, agent_rate=pnl.agent_rate + Decimal('100'),
                      monthly_pnl=pnl.monthly_pnl + Decimal('100'))
    paths = writer.write_settlement(changed, alternate)
    assert (original / 'summary.md').read_text() == 'published with historical rate rules'
    assert paths['summary'].is_file()
    payload = json.loads(paths['provenance'].read_text())
    assert Decimal(payload['results']['agent_rate']) == changed.agent_rate


def test_explicit_replacement_is_possible(tmp_path, pnl):
    writer.write_settlement(pnl, tmp_path)
    before = (tmp_path / 'provenance.json').read_bytes()
    changed = replace(pnl, agent_rate=pnl.agent_rate + Decimal('100'),
                      monthly_pnl=pnl.monthly_pnl + Decimal('100'))
    writer.write_settlement(changed, tmp_path, allow_overwrite=True)
    assert (tmp_path / 'provenance.json').read_bytes() != before
    payload = json.loads((tmp_path / 'provenance.json').read_text())
    assert Decimal(payload['results']['agent_rate']) == changed.agent_rate


@pytest.mark.parametrize('cutoff', [None, '2026-09-30'])
def test_cli_blocks_saved_month_before_any_source_reads(tmp_path, monkeypatch, cutoff):
    (tmp_path / 'summary.md').write_text('original')

    def forbidden(*args, **kwargs):
        pytest.fail('Blocked CLI run reached monthly extraction/computation')

    monkeypatch.setattr('settle.compute.compute_monthly_pnl', forbidden)
    args = ['run', '--prime', 'grove', '--month', '2026-09', '--output-dir', str(tmp_path)]
    if cutoff:
        args += ['--as-of', cutoff]
    with pytest.raises(FileExistsError):
        cli.main(args)
    assert (tmp_path / 'summary.md').read_text() == 'original'


def test_cli_passes_explicit_overwrite_to_writer(tmp_path, pnl):
    (tmp_path / 'summary.md').write_text('original')
    from unittest.mock import patch
    with patch('settle.compute.compute_monthly_pnl', return_value=pnl):
        assert cli.main(['run', '--prime', 'grove', '--month', '2026-09',
                         '--output-dir', str(tmp_path), '--allow-overwrite']) == 0
    assert (tmp_path / 'summary.md').read_text() != 'original'


def test_cli_preview_leaves_saved_month_untouched(tmp_path, pnl):
    (tmp_path / 'summary.md').write_text('published full month')
    cutoff = date(2026, 9, 15)
    preview = replace(pnl, period=Period.from_month(pnl.month, as_of=cutoff))
    from unittest.mock import patch
    with patch('settle.compute.compute_monthly_pnl', return_value=preview):
        assert cli.main(['run', '--prime', 'grove', '--month', '2026-09',
                         '--output-dir', str(tmp_path), '--as-of', str(cutoff)]) == 0
    assert (tmp_path / 'summary.md').read_text() == 'published full month'
    assert (tmp_path / 'as-of/2026-09-15/summary.md').is_file()


@pytest.fixture
def live_runner():
    spec = importlib.util.spec_from_file_location('live_safety_runner', ROOT / 'scripts/run_live_2026.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_batch_preflights_every_month_before_sources_or_partial_writes(tmp_path, live_runner, monkeypatch):
    saved = tmp_path / 'grove/2026-07'
    saved.mkdir(parents=True)
    (saved / 'summary.md').write_text('published July')

    def forbidden(*args, **kwargs):
        pytest.fail('Batch reached extraction before checking every destination')

    monkeypatch.setattr(live_runner, '_check_env', forbidden)
    monkeypatch.setattr(live_runner, 'compute_monthly_pnl', forbidden)
    monkeypatch.setattr(sys, 'argv', ['run_live_2026.py', '--primes', 'grove',
                                    '--months', '2026-06,2026-07', '--output-root', str(tmp_path)])
    with pytest.raises(FileExistsError):
        live_runner.main()
    assert not (tmp_path / 'grove/2026-06').exists()
    assert (saved / 'summary.md').read_text() == 'published July'


@pytest.mark.parametrize('mode', ['flag', 'environment'])
def test_batch_isolated_root_and_explicit_overwrite(tmp_path, live_runner, pnl, monkeypatch, mode):
    root = tmp_path / 'reconciliation'
    args = ['run_live_2026.py', '--primes', 'grove', '--months', '2026-09']
    monkeypatch.setenv('SETTLE_OUTPUT_DIR', str(tmp_path / 'unused-root'))
    if mode == 'flag':
        args += ['--output-root', str(root)]
    else:
        monkeypatch.setenv('SETTLE_OUTPUT_DIR', str(root))
    monkeypatch.setattr(sys, 'argv', args)
    monkeypatch.setattr(live_runner, '_check_env', lambda: None)
    monkeypatch.setattr(live_runner, '_check_envio_token', lambda prime: None)
    monkeypatch.setattr(live_runner, 'resolved_source_labels', lambda *args: {})
    monkeypatch.setattr(live_runner, 'compute_monthly_pnl', lambda *args, **kwargs: pnl)
    assert live_runner.main() == 0
    assert (root / 'grove/2026-09/summary.md').is_file()
    assert not (tmp_path / 'unused-root').exists()
    # A repeated calculation still needs an explicit decision even in an audit folder.
    with pytest.raises(FileExistsError):
        live_runner.main()
    monkeypatch.setattr(sys, 'argv', [*args, '--allow-overwrite'])
    assert live_runner.main() == 0


@pytest.mark.parametrize('allow_overwrite', [False, True])
def test_existing_writer_lock_cannot_be_bypassed(tmp_path, pnl, monkeypatch, allow_overwrite):
    lock = tmp_path / '.settlement-write.lock'
    lock.write_text('pid=another-writer\n')

    def forbidden(*args, **kwargs):
        pytest.fail('A competing writer reached enrichment')

    monkeypatch.setattr(writer, 'enrich_with_dr', forbidden)
    with pytest.raises(FileExistsError, match='writer lock'):
        writer.write_settlement(pnl, tmp_path, allow_overwrite=allow_overwrite)
    assert {p.name: p.read_text() for p in tmp_path.iterdir()} == {lock.name: 'pid=another-writer\n'}


def test_simultaneous_reruns_cannot_both_write(tmp_path, pnl, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    ready = Barrier(2)
    check = writer.check_settlement_destination

    def both_pass_preflight(path, **kwargs):
        check(path, **kwargs)
        ready.wait(timeout=10)

    monkeypatch.setattr(writer, 'check_settlement_destination', both_pass_preflight)
    first = replace(pnl, agent_rate=pnl.agent_rate + Decimal('100'),
                    monthly_pnl=pnl.monthly_pnl + Decimal('100'))
    second = replace(pnl, agent_rate=pnl.agent_rate + Decimal('200'),
                     monthly_pnl=pnl.monthly_pnl + Decimal('200'))
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(writer.write_settlement, result, tmp_path)
                   for result in [first, second]]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=15))
            except FileExistsError as error:
                outcomes.append(error)
    assert sum(isinstance(result, dict) for result in outcomes) == 1
    assert sum(isinstance(result, FileExistsError) for result in outcomes) == 1
    payload = json.loads((tmp_path / 'provenance.json').read_text())
    assert Decimal(payload['results']['agent_rate']) in {first.agent_rate, second.agent_rate}
    assert not (tmp_path / '.settlement-write.lock').exists()


def test_finished_writer_between_preflight_and_lock_does_not_get_overwritten(tmp_path, pnl, monkeypatch):
    from contextlib import contextmanager

    acquire = writer._settlement_write_lock

    @contextmanager
    def completed_in_the_meantime(path):
        (path / 'summary.md').write_text('another completed settlement')
        with acquire(path):
            yield

    monkeypatch.setattr(writer, '_settlement_write_lock', completed_in_the_meantime)
    with pytest.raises(FileExistsError, match='artifacts already exist'):
        writer.write_settlement(pnl, tmp_path)
    assert (tmp_path / 'summary.md').read_text() == 'another completed settlement'
    assert not (tmp_path / 'provenance.json').exists()
    assert not (tmp_path / '.settlement-write.lock').exists()


@pytest.mark.parametrize('failure_stage', ['enrichment', 'summary'])
def test_writer_releases_its_lock_on_failure(tmp_path, pnl, monkeypatch, failure_stage):
    target = 'enrich_with_dr' if failure_stage == 'enrichment' else 'write_summary'
    original = getattr(writer, target)

    def fail(*args, **kwargs):
        raise RuntimeError('simulated rendering failure')

    monkeypatch.setattr(writer, target, fail)
    with pytest.raises(RuntimeError, match='simulated rendering failure'):
        writer.write_settlement(pnl, tmp_path)
    assert not (tmp_path / '.settlement-write.lock').exists()
    monkeypatch.setattr(writer, target, original)
    # Partial artifacts still require deliberate replacement after a failed write.
    paths = writer.write_settlement(pnl, tmp_path, allow_overwrite=True)
    assert paths['summary'].is_file()
    assert not (tmp_path / '.settlement-write.lock').exists()


@pytest.mark.parametrize('selection', [
    ['--months', '2026-09,2026-9'],
    ['--primes', 'grove,grove'],
    ['--months', '2026-09,2026-13'],
    ['--months', '2026-09,0000-09'],
])
def test_invalid_or_duplicate_batch_selection_fails_before_any_work(
    tmp_path, live_runner, monkeypatch, selection,
):
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid batch reached source checks or computation')

    monkeypatch.setattr(live_runner, '_check_env', forbidden)
    monkeypatch.setattr(live_runner, 'compute_monthly_pnl', forbidden)
    monkeypatch.setattr(sys, 'argv', ['run_live_2026.py', '--output-root', str(tmp_path), *selection])
    with pytest.raises(ValueError):
        live_runner.main()
    assert not list(tmp_path.iterdir())
