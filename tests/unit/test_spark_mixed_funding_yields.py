"""Daily Savings/Sky shares change on refinancing, not just asset deposits."""
import importlib.util
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from settle.compute.allocation_capital import replay_history
from settle.normalize.allocation_capital import (
    AssetMovement,
    CapitalBatch,
    CapitalHistory,
    ExternalFundingOperation,
)


def history():
    return CapitalHistory((
        CapitalBatch('save', date(2026, 8, 1), 1, 'ethereum', 1,
            (AssetMovement('venue', D(0), D(100)),),
            external_funding=(ExternalFundingOperation('draw', 'saver', D(100)),)),
        CapitalBatch('refinance', date(2026, 8, 2), 2, 'ethereum', 2,
            (AssetMovement('venue', D(110), D(0)),), D(55), minted_by_ilk={'spark': D(55)},
            external_funding=(ExternalFundingOperation('repay', 'saver', D(50)),
                              ExternalFundingOperation('interest', 'saver', D(5))),
            funding_assumption='Provisional proportional refinancing'),
    ), {'S1': 'venue'}, {})


def test_sparse_snapshots_preserve_daily_sources_and_do_not_change_existing_replay():
    h = history()
    start, end = date(2026, 8, 1), date(2026, 8, 2)
    plain = replay_history(h, start, end)
    split = replay_history(h, start, end, funding_accounts={'venue'})
    assert plain.daily_funding == {}
    assert plain.daily == split.daily and plain.daily_by_ilk == split.daily_by_ilk
    assert plain.ledger == split.ledger
    assert set(split.daily_funding[end]) == {'venue'}
    first, last = split.daily_funding[start]['venue'], split.daily_funding[end]['venue']
    assert first['sky_principal'] == 0 and first['savings_by_source'] == {'saver': D(100)}
    assert last['sky_principal'] == 50 and last['savings_by_source'] == {'saver': D(50)}
    assert last['observed_value'] == 110  # 10 earned does not become funded principal.
    assert last['uncertain']
    assert first['savings_by_source']['saver'] == 100  # Snapshot isn't an alias of mutable state.
    assert split.ledger.account('financing:saver-interest:saver').borrowed == 5


def test_conditional_yields_keep_gaps_and_never_become_publishable(monkeypatch):
    monkeypatch.syspath_prepend(str(Path('scripts').resolve()))
    spec = importlib.util.spec_from_file_location('mixed_yields', 'scripts/audit_spark_mixed_funding_yields.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    days = ['2026-08-01', '2026-08-02']
    ready = {'prime': 'spark', 'start': days[0], 'end': days[1], 'allocations': [
        {'venue': 'S1', 'label': 'SparkLend', 'status': 'savings_split_pending', 'cost_of_funds': None,
         'daily_principal': {d: '100' for d in days}}]}
    control = {'prime_id': 'spark', 'period': {'start': days[0], 'end': days[1]},
        'sky_revenue_daily': [{'date': d, 'utilized': '100', 'daily_sky_rev': '1', 'lending_idle': '20'} for d in days],
        'venue_breakdown': [{'venue_id': 'S1', 'revenue': '1', 'actual_revenue': '1', 'external_revenue': '0',
                             'tw_avg_value_usd': '110', 'lending_idle_tw_avg_usd': '20'}]}
    h = history()
    replay = replay_history(h, date(2026, 8, 1), date(2026, 8, 2), funding_accounts={'venue'})
    result = module.summarize(replay, h, ready, control, {'daily': {'S1': {d: '20' for d in days}}}, ['S1'])
    assert result['publishable'] is False
    row = result['allocations'][0]
    assert row['conditional_sky_borrowing_cost'] == D('.3')
    assert row['average_sky_principal'] == 25
    assert row['average_savings_principal'] == 75
    assert row['capital_gap_days'] == [] and row['uncertain_days'] == ['2026-08-02']
    assert row['conditional_net_apy'] is not None
    ready['allocations'][0]['daily_principal'][days[1]] = '105'
    result = module.summarize(replay, h, ready, control, {'daily': {'S1': {d: '20' for d in days}}}, ['S1'])
    assert result['allocations'][0]['capital_gap_days'] == ['2026-08-02']
