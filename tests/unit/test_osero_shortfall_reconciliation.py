"""Frozen cashflow replay and settlement booking, without live RPC dependencies."""
import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('osero_reconcile', ROOT / 'scripts/reconcile_osero_shortfalls.py')
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


def test_refund_matches_executed_cashflows_and_october_booking():
    result = replay.reconcile()
    assert result == json.loads((ROOT / 'reconciliation/2026-10/osero_shortfalls.json').read_text())
    assert [r['send_credit'] for r in result['rows']] == ['106', '1448', '3960']
    assert result['send_credit'] == '5514'
    config = yaml.safe_load((ROOT / 'config/sky_total.yaml').read_text())
    adjustments = config['msc_preview']['2026-10']['osero']
    assert set(adjustments) <= {'send_credit', 'sky_adj', 'sv_adj', 'dv_adj'}
    assert Decimal(adjustments['send_credit']) == Decimal('5514')
    assert all(Decimal(value) == 0 for key, value in adjustments.items() if key != 'send_credit')
    assert sum((Decimal(r['send_credit']) for r in result['rows']), Decimal(0)) == Decimal(adjustments['send_credit'])


def test_replay_refuses_changed_historical_workbooks(tmp_path):
    source = ROOT / replay.EVIDENCE
    target = tmp_path / replay.EVIDENCE
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    item = json.loads(source.read_text())['rows'][0]
    workbook = tmp_path / item['workbook']
    workbook.parent.mkdir(parents=True)
    workbook.write_bytes(b'changed')
    with pytest.raises(ValueError, match='Historical workbook changed'):
        replay.reconcile(tmp_path)


def test_replay_refuses_payment_that_does_not_match_historical_formula(monkeypatch):
    original = replay.components

    def changed_demand(path):
        revenue, demand, cof = original(path)
        return revenue, demand + Decimal('10'), cof

    monkeypatch.setattr(replay, 'components', changed_demand)
    with pytest.raises(ValueError, match='Executed cashflows do not match'):
        replay.reconcile()
