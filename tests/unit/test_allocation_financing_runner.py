import importlib.util
import json
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from settle.compute._helpers import ssr_at_or_before
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory


def test_financing_runner_preserves_control_and_passes_ssr_schema(monkeypatch, tmp_path):
    path = Path(__file__).parents[2] / 'scripts/validate_allocation_financing.py'
    spec = importlib.util.spec_from_file_location('financing_runner', path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    day = date(2026, 8, 1)
    control = {
        'prime_id': 'obex', 'period': {'start': str(day), 'end': str(day)},
        'pin_blocks_eom': {'ethereum': 1},
        'venue_breakdown': [{'venue_id': 'V1', 'label': 'Vault', 'value_som': '100',
            'value_eom': '100', 'period_inflow': '0', 'revenue': '2', 'actual_revenue': '2',
            'tw_avg_value_usd': '100'}],
        'sky_revenue_daily': [{'date': str(day), 'cum_debt': '100', 'utilized': '100',
            'daily_sky_rev': '1', 'base_apr': '3.65', 'ssr_apy': '0.03'}],
        'results': {'sky_revenue': '1', 'sde_revenue': '0', 'susds_spread_reimbursement': '0'},
    }
    provenance = tmp_path / 'provenance.json'
    provenance.write_text(json.dumps(control))
    before = provenance.read_bytes()
    (tmp_path / 'pins.json').write_text(json.dumps({'prime': 'obex', 'start': str(day),
        'end': str(day), 'pins': {'ethereum': 1}}))
    monkeypatch.setattr('sys.argv', [str(path), '--provenance', str(provenance),
                                   '--history-dir', str(tmp_path)])
    history = CapitalHistory((CapitalBatch('draw', day, 1, 'ethereum', 1,
                (AssetMovement('vault', D(0), D(100)),), D(100)),), {'V1': 'vault'}, {})
    monkeypatch.setattr(runner, 'load_history', lambda *args: history)
    monkeypatch.setattr(runner, 'fingerprint', lambda *args: 'key')

    def curve(*args, **kwargs):
        assert ssr_at_or_before(kwargs['ssr_history'], day) == D('0.03')

    monkeypatch.setattr(runner, '_aggregate_curve_idle_usds', curve)
    monkeypatch.setattr(runner, '_aggregate_univ4_idle_usds', lambda *a, **k: None)
    monkeypatch.setattr(runner, '_aggregate_lending_idle_usds', lambda *a, **k: None)
    runner.main()
    assert provenance.read_bytes() == before
    result = json.loads((tmp_path / 'financing.json').read_text())
    assert result['reconciliation']['complete'] is True
    assert D(result['reconciliation']['difference']) == 0
    assert result['allocations'][0]['gross_apy'] is not None
