from datetime import date
from decimal import Decimal

import pytest

from settle.revenue.financial import ROLLUP_POLICY, decode_selection, metric, statements
from settle.revenue.statement_rollups import combine
from tests.unit.test_financial_reporting import publication


def report():
    return statements('obex', 2026, date(2026, 10, 28),
                      [publication('2026-09-30'), publication()], rollups=True)


def by_id(document, key):
    return next(row for row in document['lines'] if row['id'] == key)


def test_rollups_use_disjoint_leaves_and_preserve_signed_partial_values():
    doc = report()
    assert doc['policy_version'] == ROLLUP_POLICY
    cells = {row['id']: row['values']['2026-10'] for row in doc['lines']}
    assert cells['statement.interest']['known_value'] == '100'
    assert cells['statement.family.maple']['value'] == '100'
    assert cells['statement.funding']['known_value'] == '100'
    assert cells['statement.net_interest']['known_value'] == '0'
    assert cells['statement.demand']['known_value'] == '12.000000000000000001'
    assert cells['statement.net_revenue']['known_value'] == '12.000000000000000001'
    net = cells['statement.net_income']
    assert net['value'] is None and net['status'] == 'partial'
    assert 'unclassified.income_tax' in net['missing_components']
    assert 'unclassified.credit_impairment' in net['missing_components']
    for row in doc['lines']:
        if 'components' in row:
            ids = [component['id'] for component in row['components']]
            assert len(set(ids)) == len(ids)
            assert 'investment.reviewed_income' not in ids
            assert 'rewards.reviewed_lending' not in ids
            assert not any(key.startswith('statement.family.') for key in ids)


def test_complete_reviewed_group_and_partial_entity_parent_are_distinct():
    doc = report()
    group = by_id(doc, 'statement.family.maple')
    assert group['values']['2026-09']['value'] == '100'
    assert by_id(doc, 'statement.interest')['values']['2026-09']['status'] == 'partial'
    assert group['total']['value'] is None  # Jan-Aug allocation history is missing
    assert group['total']['known_value'] == '200'
    assert by_id(doc, 'statement.net_interest')['total']['known_value'] is not None


def test_published_demand_total_overrides_independent_display_rounding():
    doc = statements('spark', 2026, date(2026, 8, 31), [], rollups=True)
    demand = by_id(doc, 'statement.demand')
    published = by_id(doc, 'demand.total')
    for month in range(1, 9):
        key = f'2026-{month:02}'
        assert demand['values'][key]['value'] == published['values'][key]['value']
        assert demand['values'][key]['component_ids'] == ['demand.total']
    assert demand['total']['status'] == 'available'
    assert Decimal(demand['total']['value']) == sum(Decimal(published['values'][f'2026-{m:02}']['value']) for m in range(1, 9))


def test_partial_months_are_included_once_in_annual_known_components():
    partial = metric()
    partial.update(status='partial', known_value='0', missing_components=['unknown'])
    value = combine([('month1', 1, partial), ('month2', 1, metric('2'))])
    assert value['value'] is None and value['known_value'] == '2'
    negative = combine([('income', 1, metric('1')), ('funding', -1, metric('2'))])
    assert negative['value'] == '-1'
    assert combine([('zero', 1, metric('0'))])['value'] == '0'


def test_partial_multi_deployment_business_line_is_counted_once_in_every_rollup():
    from settle.revenue.financial import build
    from settle.revenue.verification import digest
    from tests.unit.test_financial_allocations import allocation_source
    saved = allocation_source(missing=['S65'])
    pub = dict(cutoff=saved['cutoff'], revision_id=digest(saved), document=build(saved))
    doc = statements('spark', 2026, date(2026, 10, 28), [pub], rollups=True)
    for key, amount in [('investment.morpho.blue_chip_usdt', '10'),
                        ('statement.family.morpho_vaults', '50'), ('statement.interest', '240')]:
        row = by_id(doc, key)
        assert row['values']['2026-10']['known_value'] == amount
        assert row['total']['known_value'] == amount
        assert row['total']['value'] is None
    assert by_id(doc, 'statement.rewards')['values']['2026-10']['known_value'] == '24'


def test_new_and_old_selections_remain_immutable_and_future_cells_empty():
    doc = report()
    selection = decode_selection(doc['selection'], 'obex', 2026)
    rows = [publication('2026-09-30'), publication(), publication('2026-10-28', agent_rate='999')]
    assert statements('obex', 2026, date(2026, 10, 28), rows, selection=selection) == doc
    old = statements('obex', 2026, date(2026, 10, 28), rows[:2], monthly=True)
    assert statements('obex', 2026, date(2026, 10, 28), rows, selection=decode_selection(old['selection'], 'obex', 2026), rollups=True) == old
    assert not any(row['id'].startswith('statement.') for row in old['lines'])
    for row in doc['lines']:
        assert row['values']['2026-11']['status'] == 'future'
        assert row['values']['2026-11']['known_value'] is None


@pytest.mark.parametrize('prime', ['spark', 'grove', 'obex', 'keel', 'skybase', 'osero'])
def test_every_prime_has_unique_ordered_rollups(prime):
    doc = statements(prime, 2026, date(2026, 10, 28), [], rollups=True)
    assert len(doc['lines']) == len({row['id'] for row in doc['lines']})
    defined = {row['id'] for row in doc['lines'] if not row['id'].startswith('statement.')}
    for row in doc['lines']:
        if not row['id'].startswith('statement.'):
            continue
        assert all(term['id'] in defined or term['id'].startswith('unclassified.') for term in row['components'])
        defined.add(row['id'])
