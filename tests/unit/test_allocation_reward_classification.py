from copy import deepcopy
from datetime import date

import pytest

from settle.revenue.financial import (
    ALLOCATION_REWARDS_POLICY,
    build,
    decode_selection,
    metric,
    statements,
)
from settle.revenue.statement_rollups import definitions
from settle.revenue.verification import digest
from tests.unit.test_financial_allocations import allocation_source
from tests.unit.test_financial_rwa import publication as rwa_publication


def line(doc, key):
    return next(row for row in doc['lines'] if row['id'] == key)


def known(cell):
    return cell['value'] if cell['value'] is not None else cell['known_value']


@pytest.mark.parametrize('prime', ['spark', 'grove', 'osero'])
def test_linked_rewards_move_once_without_changing_total_revenue_or_recognition(prime):
    saved = allocation_source(prime)
    document = build(saved)
    if prime == 'spark':
        document['metrics']['rewards.issuer.pyusd'] = metric('7', basis='issuer_compensation_daily_accrual')
    pub = dict(cutoff=saved['cutoff'], revision_id=digest(saved), document=document)
    before = deepcopy(pub)
    old = statements(prime, 2026, date(2026, 10, 28), [pub], rwa=True)
    new = statements(prime, 2026, date(2026, 10, 28), [pub], allocation_rewards=True)
    assert new['policy_version'] == ALLOCATION_REWARDS_POLICY
    linked = line(new, 'statement.family.allocation_rewards')
    expected = {'spark': '31', 'grove': '6', 'osero': '2'}[prime]
    assert known(linked['values']['2026-10']) == expected
    assert known(line(new, 'statement.rewards')['values']['2026-10']) is None
    moved = {c['id'] for c in linked['components']}
    assert moved <= {c['id'] for c in line(new, 'statement.interest')['components']}
    assert not moved & {c['id'] for c in line(new, 'statement.rewards')['components']}
    for row in new['lines']:
        if row.get('aggregation') == 'leaf':
            assert row['values'] == line(old, row['id'])['values']
    for key in ['statement.net_revenue', 'statement.net_income']:
        assert known(line(new, key)['total']) == known(line(old, key)['total'])
        for period in new['periods']:
            assert known(line(new, key)['values'][period['key']]) == known(line(old, key)['values'][period['key']])
    assert statements(prime, 2026, date(2026, 10, 28), [pub], allocation_rewards=True,
                      selection=decode_selection(old['selection'], prime, 2026)) == old
    assert pub == before


def test_unknown_rewards_remain_noninterest_and_never_enter_allocation_income():
    rows = definitions('spark', [
        {'id': 'rewards.lending.market', 'aggregation': 'leaf'},
        {'id': 'rewards.issuer.rlusd', 'aggregation': 'leaf'},
        {'id': 'rewards.unrelated.partnership', 'aggregation': 'leaf'},
        {'id': 'rewards.reviewed_lending', 'aggregation': 'subtotal'},
    ], allocation_rewards=True)
    assert {'id': 'rewards.unrelated.partnership', 'sign': 1} in line({'lines': rows}, 'statement.rewards')['components']
    assert {'id': 'rewards.unrelated.partnership', 'sign': 1} not in line({'lines': rows}, 'statement.interest')['components']
    assert not any(c['id'] == 'rewards.reviewed_lending' for row in rows for c in row['components'])


def test_new_classification_keeps_full_grove_rwa_history_and_old_selection():
    pub = rwa_publication()
    old = statements('grove', 2026, date(2026, 10, 28), [pub], rwa=True)
    new = statements('grove', 2026, date(2026, 10, 28), [pub], allocation_rewards=True)
    for key in ['investment.rwa.stac', 'investment.rwa.jaaa']:
        assert line(new, key) == line(old, key)
    assert line(new, 'statement.interest')['label'] == 'Investment income from allocations'
    assert statements('grove', 2026, date(2026, 10, 28), [pub], allocation_rewards=True,
                      selection=decode_selection(old['selection'], 'grove', 2026)) == old
