from copy import deepcopy
from datetime import date
from decimal import Decimal

from settle.revenue.financial import build, decode_selection, statements
from settle.revenue.financial_spark import (
    RESERVE_RECEIPTS,
    REVIEWED_CONFIGURATIONS,
    UNMAPPED_RECEIPTS,
)
from settle.revenue.verification import digest
from tests.unit.test_financial_allocations import allocation_source


def saved(*, missing=(), configuration=None):
    source = allocation_source(missing=missing)
    source['configuration_version'] = configuration or sorted(REVIEWED_CONFIGURATIONS)[0]
    return {'cutoff': source['cutoff'], 'revision_id': digest(source),
            'publication_order': 1, 'document': build(source)}


def statement(source, **kwargs):
    return statements('spark', 2026, date(2026, 10, 27), [source], **kwargs)


def cell(report, key):
    return next(row for row in report['lines'] if row['id'] == key)['values']['2026-10']


def amount(item):
    return Decimal(item['value'] or item['known_value'] or '0')


def test_reserve_receipts_reclassified_exactly_once_with_unchanged_net_revenue():
    source = saved()
    original = deepcopy(source)
    old = statement(source, archive=True)
    new = statement(source, spark_income=True)
    fees = cell(new, 'fees.sparklend')
    assert fees['status'] == 'partial' and fees['known_value'] == '10'
    assert fees['recognition'] == 'receipt_in_kind'
    assert fees['component_ids'] == list((*RESERVE_RECEIPTS, *UNMAPPED_RECEIPTS))
    assert fees['missing_components'] == list(UNMAPPED_RECEIPTS)
    assert fees['source_refs'] == [source['revision_id']]
    assert amount(cell(old, 'statement.interest')) - amount(cell(new, 'statement.interest')) == 10
    assert amount(cell(new, 'statement.noninterest_income')) - amount(cell(old, 'statement.noninterest_income')) == 10
    for key in ('statement.net_revenue', 'statement.net_income'):
        assert amount(cell(old, key)) == amount(cell(new, key))
    for key in ('fees.curator',):
        assert cell(new, key)['value'] is None  # A recipient/rate is not an earned fee amount.
    assert source == original


def test_missing_receipt_component_is_partial_and_unknown_config_is_not_certified():
    new = statement(saved(missing=('S2',)), spark_income=True)
    fee = cell(new, 'fees.sparklend')
    assert fee['status'] == 'partial' and fee['known_value'] == '8'
    assert set(fee['missing_components']) == {'rewards.lending.sparklend.usdc', *UNMAPPED_RECEIPTS}
    new = statement(saved(configuration='unreviewed'), spark_income=True)
    fee = cell(new, 'fees.sparklend')
    assert fee['value'] is None and fee['known_value'] is None
    assert cell(new, 'rewards.unattributed_sparklend_receipts')['value'] == '10'
    old = statement(saved(configuration='unreviewed'), archive=True)
    assert amount(cell(old, 'statement.net_revenue')) == amount(cell(new, 'statement.net_revenue'))


def test_retained_old_policy_is_unchanged_and_new_selection_replays():
    source = saved()
    for flags in ({'archive': True}, {'spark_income': True}):
        old = statement(source, **flags)
        selection = decode_selection(old['selection'], 'spark', 2026)
        assert statement(source, selection=selection, spark_income=True) == old
    new = statement(source, spark_income=True)
    line = next(row for row in new['lines'] if row['id'] == 'fees.sparklend')
    assert line['values']['2026-01']['value'] is None
    residual = next(row for row in new['lines'] if row['id'] == 'rewards.unattributed_sparklend_receipts')
    assert 'source_ref' not in residual['values']['2026-01']
    assert line['values']['2026-11']['status'] == 'future'
    assert line['total']['status'] == 'partial'
