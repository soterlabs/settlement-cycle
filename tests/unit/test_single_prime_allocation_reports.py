"""Independent Osero/Obex publication uses principal, not appreciated NAV."""
import gzip
import hashlib
import json
import sys
from datetime import date, timedelta
from decimal import Decimal as D

import pytest
import report_allocation_capital_readiness as capital
import report_allocation_yields as yields
from publish_monthly_allocations import prepare

from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory
from settle.normalize.allocation_history_cache import save_history


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
