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
