"""Independent Osero/Obex publication uses principal, not appreciated NAV."""
import gzip
import hashlib
import importlib.util
import json
import sys
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory
from settle.normalize.allocation_history_cache import save_history


def script(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[2] / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capital = script('report_allocation_capital_readiness')
yields = script('report_allocation_yields')
prepare = script('publish_monthly_allocations').prepare


@pytest.mark.parametrize('prime,venue,idle', [('osero', 'O1', '20'), ('obex', 'V1', '0')])
def test_single_prime_reports_and_publication(tmp_path, monkeypatch, prime, venue, idle):
    start, end = date(2026, 9, 1), date(2026, 9, 30)
    days = [str(start + timedelta(days=i)) for i in range(30)]
    control_path = tmp_path / prime / '2026-09' / 'provenance.json'
    control_path.parent.mkdir(parents=True)
    control = dict(prime_id=prime, period=dict(start=str(start), end=str(end)),
                   pin_blocks_eom={'ethereum': 100},
                   sky_revenue_daily=[dict(date=d, daily_sky_rev='1', utilized='100', lending_idle=idle) for d in days],
                   venue_breakdown=[dict(venue_id=venue, revenue='3', actual_revenue='3', external_revenue='0',
                                        tw_avg_value_usd='110', lending_idle_tw_avg_usd=idle)])
    control_path.write_text(json.dumps(control))
    h = CapitalHistory((CapitalBatch('deposit', start, 1, 'ethereum', 1,
                         (AssetMovement('position', D(0), D(100)),)),), {venue: 'position'}, {})
    history = tmp_path / 'history.jsonl.gz'
    save_history(history, h, 'saved-key')
    manifest = dict(prime=prime, end=str(end), pins=control['pin_blocks_eom'],
                    history_sha256=hashlib.sha256(history.read_bytes()).hexdigest())
    (tmp_path / 'pins.json').write_text(json.dumps(manifest))
    idle_path = tmp_path / 'idle.json'
    idle_path.write_text(json.dumps(dict(control_sha256=hashlib.sha256(control_path.read_bytes()).hexdigest(),
                                       daily={venue: {d: idle for d in days}})))
    ready, result = tmp_path / 'ready.gz', tmp_path / 'yield.json'
    monkeypatch.setattr(sys, 'argv', ['report', '--prime', prime, '--month', '2026-09',
        '--history', str(history), '--idle-deductions', str(idle_path), '--settlements-root', str(tmp_path),
        '--output', str(ready), '--markdown', str(tmp_path / 'capital.md')])
    capital.main()
    row = next(r for r in json.loads(gzip.decompress(ready.read_bytes()))['results'][0]['allocations'] if r['venue'] == venue)
    assert D(row['average_principal']) == 100  # 110 NAV does not add borrowed principal.
    assert D(row['cost_of_funds']) == (D(100)-D(idle))*D('.01')*30
    monkeypatch.setattr(sys, 'argv', ['yields', '--month', '2026-09', '--settlements-root', str(tmp_path),
        '--readiness', str(ready), '--output', str(result), '--markdown', str(tmp_path / 'yields.md')])
    yields.main()
    documents = prepare(ready, result)
    assert [d['prime'] for d in documents] == [prime]
    output = next(r for r in documents[0]['allocations'] if r['venue_id'] == venue)
    assert D(output['average_yield_exposure_usd']) == 110
    assert D(output['gross_apy']) > 0
    assert D(output['net_apy']) < 0
    # Pin/hash identity is required even when running a single historical prime.
    manifest['history_sha256'] = 'stale'
    (tmp_path / 'pins.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(sys, 'argv', ['report', '--prime', prime, '--month', '2026-09',
        '--history', str(history), '--idle-deductions', str(idle_path), '--settlements-root', str(tmp_path),
        '--output', str(ready), '--markdown', str(tmp_path / 'capital.md')])
    with pytest.raises(ValueError, match='settlement pins'):
        capital.main()


@pytest.mark.parametrize('prime', ['osero', 'obex'])
def test_september_frozen_inputs_reproduce_published_candidate(prime):
    from settle.normalize.allocation_history_cache import load_history
    root = Path('reconciliation/monthly-allocation-inputs') / prime / '2026-09'
    history_path = root / 'history.jsonl.gz'
    with gzip.open(history_path, 'rt') as f:
        key = json.loads(next(f))['fingerprint']
    history = load_history(history_path, key)
    control_path = Path('reconciliation/monthly-allocation-controls') / prime / '2026-09/provenance.json'
    control = json.loads(control_path.read_text())
    idle = json.loads((root / 'idle_deductions.json').read_text())
    assert idle['control_sha256'] == hashlib.sha256(control_path.read_bytes()).hexdigest()
    capital.validate_history_manifest(json.loads((root / 'pins.json').read_text()), prime=prime,
        end=date(2026, 9, 30), control=control, history_sha256=hashlib.sha256(history_path.read_bytes()).hexdigest())
    computed = capital.report(prime, history, control, idle, date(2026, 9, 1), date(2026, 9, 30))
    saved = json.loads(gzip.decompress(Path(f'reconciliation/allocation_capital_readiness_{prime}_2026_09.json.gz').read_bytes()))
    assert computed == saved['results'][0]
    candidate = json.loads(Path(f'reconciliation/allocation_yields_{prime}_2026_09.json').read_text())
    assert yields.build(saved, {prime: control})['results'] == candidate['results']
    # Global financing and original-capital costs intentionally remain distinct.
    total = sum((D(r['cost_of_funds']) for r in computed['allocations']), D(0))
    assert D(control['results']['sky_revenue']) > total
