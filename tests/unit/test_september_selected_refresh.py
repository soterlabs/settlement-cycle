"""The two-venue close must stay offline and preserve every other venue."""
import importlib.util
from pathlib import Path

import pytest
import requests

from settle.revenue.verification import canonical

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('selected_refresh', ROOT / 'scripts/refresh_september_selected_venues.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_real_snapshots_refresh_offline_and_repeat_without_double_booking(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Isolated calculation must not fetch inputs or publish API results')
    monkeypatch.setattr(requests.sessions.Session, 'send', forbidden)
    from settle.revenue import store
    monkeypatch.setattr(store, 'publish', forbidden)
    first, audit = module.refresh()
    second, repeated = module.refresh()
    assert audit == repeated
    assert canonical(first) == canonical(second)
    assert audit['api_published'] is False
    assert audit['venues']['spark']['after']['external_revenue'] == '219616.849890887679914591'
    assert audit['venues']['grove']['after']['actual_revenue'] == '733817.50077700'
    assert audit['venues']['grove']['after']['redemption_revenue_adjustment'] == '-12504.626548'
    grove = audit['venues']['grove']
    assert len(grove['after']['redemption_capital_outflows']) == 3
    assert grove['revenue_bridge'] == {
        'opening_position_markdown': '-321627.21088500',
        'remaining_nav_flow_repricing': '-532.74179000',
        'cash_realization': '-12504.626548',
        'restored_small_capital_outflows': '2998.5000',
    }
    for prime, (result, _) in first.items():
        _, baseline = module.baseline(prime)
        selected = audit['venues'][prime]['venue_id']
        assert result.display_only_breakdown == baseline.display_only_breakdown
        for a, b in zip(result.venue_breakdown, baseline.venue_breakdown, strict=True):
            if a.venue_id != selected:
                assert a == b
        for field in ('agent_rate', 'chronicle_points', 'gar', 'distribution_rewards', 'sky_revenue_gross',
                      'susds_spread_reimbursement', 'pin_blocks_som', 'period'):
            assert getattr(result, field) == getattr(baseline, field)
    assert first['spark'][0].sky_revenue_daily == module.baseline('spark')[1].sky_revenue_daily


def test_mutated_or_already_refreshed_baseline_rejected(tmp_path, monkeypatch):
    path = tmp_path / 'spark-baseline.json'
    path.write_text((module.DATA / path.name).read_text() + ' ')
    monkeypatch.setattr(module, 'DATA', tmp_path)
    with pytest.raises(ValueError, match='immutable'):
        module.baseline('spark')
