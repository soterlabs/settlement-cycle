import gzip
import importlib
import json
from collections import Counter
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_history_cache import load_history

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures'
ROOT = FIXTURES.parents[1]
START, END = date(2026, 1, 1), date(2026, 8, 31)


@pytest.fixture(scope='module')
def modules():
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(ROOT / 'scripts'))
        yield (importlib.import_module('audit_scoped_allocation_origin'),
               importlib.import_module('audit_origin_custody_routes'))


@pytest.mark.parametrize('prime,venue,group,bounded,capital,borrowed', [
    ('spark','S1',('S1',),True,'681732615.54','662616464.85'),
    ('grove','E9',('E9','E8','E20','E15'),False,'841108582.40','839775712.20'),
])
def test_actual_scoped_results_and_monthly_cutoffs(modules, prime, venue, group, bounded, capital, borrowed):
    module, _ = modules
    tag = 'spark_spusds' if prime == 'spark' else 'grove_jtrsy'
    key = 'spark-spusds-origin' if prime == 'spark' else 'grove-jtrsy-origin'
    h = load_history(FIXTURES / f'{tag}_origin_history.jsonl.gz', key)
    result = module.attribute(h, venue, group, START, END, bound_external_funding=bounded)
    assert D(result['closing_capital']).quantize(D('.01')) == D(capital)
    assert D(result['closing_borrowed']).quantize(D('.01')) == D(borrowed)
    assert len(result['monthly']) == 8
    assert abs(sum(D(r['unclassified_closing_capital']) for r in result['remaining_sources'])
               - D(result['monthly'][-1]['unclassified'])) < D('1e-8')
    # The compact fixture must reproduce the full-history audit, including
    # custody/source rankings, not only rounded closing totals.
    artifact = json.load(gzip.open(ROOT / f'reconciliation/{tag}_funding_origins_2026_08.json.gz'))
    for k in ('monthly','remaining_sources','closing_capital','closing_borrowed'):
        assert result[k] == artifact[k]
    for day in (date(2026,1,31), date(2026,4,30)):
        cutoff = module.attribute(h, venue, group, START, day, bound_external_funding=bounded)
        assert cutoff['monthly'][-1] == next(r for r in result['monthly'] if r['month_end'] == str(day))


def test_registered_custody_routes_preserve_movements_debt_and_local_capital(modules):
    module, custody = modules
    from audit_allocation_positions import audit
    from audit_scoped_borrowing_origins import trace
    h = load_history(FIXTURES / 'grove_jtrsy_origin_history.jsonl.gz', 'grove-jtrsy-origin')
    group = ('E9','E8','E20','E15')
    separated, proofs = custody.separate(h, group)
    assert proofs
    assert Counter(m for b in h.batches for m in b.movements) == Counter(m for b in separated.batches for m in b.movements)
    assert sum(b.minted for b in h.batches) == sum(b.minted for b in separated.batches)
    again, _ = custody.separate(separated, group)
    assert again == separated
    for v in ('E9','E8','E20'):
        assert audit(h,v,START,END)['daily'] == audit(separated,v,START,END)['daily']
    tx = 'ethereum:0x4fa9ac01fe73c9bbf803213177400d10558d19fa3bdbfb14eeb315b503449749'
    # Independent JAAA issuance and JTRSY redemption must stay in their own
    # accounts instead of sharing one borrowed-funding ratio.
    routes = [b for b in separated.batches if b.identity.startswith(tx+':origin-custody:')]
    assert len(routes) == 2
    for b in routes:
        assert b.minted == 0
        assert len(b.movements) == 2
        assert sum(m.change for m in b.movements) == 0
    # The unseparated broader ledger cannot be assigned a false residual
    # contribution ranking: incompatible clearing must fail explicitly.
    local = audit(h,'E9',START,END)
    origin = trace(h,group,END)
    accounts = {h.venue_accounts['E9'],*h.custody_accounts['E9']}
    with pytest.raises(ValueError, match='mixed routing'):
        module.rank_residual_sources(h,accounts,local,origin,END)


def test_monthly_snapshots_do_not_use_future_events_and_zero_is_not_full_coverage(modules):
    module, _ = modules
    local = {'daily': {'2026-01-31': '0','2026-02-28':'100'}}
    origin = {'transactions': [{'day':'2026-02-01','borrowed_by_account':{'a':'80'}}]}
    rows = module.snapshots(local,origin,{'a'},date(2026,1,1),date(2026,2,28))
    assert rows[0]['borrowed_percent'] is None
    assert rows[0]['borrowed'] == '0'
    assert rows[1]['borrowed_percent'] == '80.0'
