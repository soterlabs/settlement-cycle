import copy
import json
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path('scripts').resolve()))
from refresh_spark_september_allocation_revenue import FOLDER, TARGETS, refresh


def inputs():
    return [json.loads((FOLDER / n).read_text()) for n in ('baseline.json', 'evidence.json')]


def test_refresh_changes_only_target_revenue_and_preserves_costs():
    b, e = inputs()
    original = copy.deepcopy(b)
    result = refresh(b, e)
    assert b == original
    for old, new in zip(b['allocations'], result['allocations'], strict=True):
        assert old['borrowing_cost_usd'] == new['borrowing_cost_usd']
        if old['venue_id'] not in TARGETS:
            assert old == new
        else:
            assert D(new['net_pnl_usd']) == D(new['prime_retained_revenue_usd']) - D(old['borrowing_cost_usd'])
    rows = {r['venue_id']: r for r in result['allocations']}
    assert D(rows['S64']['gross_revenue_usd']) == D('1373208')
    assert D(rows['S70']['gross_revenue_usd']) == D('2400.001564107802276268')
    assert D(rows['S72']['gross_revenue_usd']) == D('3674.1568632270234742260')
    assert rows['S67']['gross_revenue_usd'] is None
    assert result['reconciliation'] == b['reconciliation']


def test_rejects_edited_baseline():
    b, e = inputs()
    b['allocations'][0]['borrowing_cost_usd'] = '0'
    with pytest.raises(ValueError, match='Baseline'):
        refresh(b, e)


@pytest.mark.parametrize('field,value', [('block_time', 1790812800), ('topic1', '0x00'), ('address', '0x00')])
def test_receipts_must_match_period_payer_and_token(field, value):
    b, e = inputs()
    e['receipts'][0][field] = value
    with pytest.raises(ValueError, match='receipt'):
        refresh(b, e)


def test_no_double_counting_duplicate_receipts():
    b, e = inputs()
    e['receipts'].append(e['receipts'][0])
    with pytest.raises(ValueError, match='duplicate'):
        refresh(b, e)


def test_refreshed_document_cannot_be_applied_again():
    from settle.revenue.verification import digest
    b, e = inputs()
    new = refresh(b, e)
    new['revision_id'] = digest(new)
    new['published_at'] = b['published_at']
    with pytest.raises(ValueError, match='Baseline'):
        refresh(new, e)


def test_only_revenue_and_yield_fields_change_for_target_rows():
    b, e = inputs()
    result = refresh(b, e)
    allowed = {'gross_revenue_usd', 'prime_retained_revenue_usd', 'net_pnl_usd',
               'gross_monthly_return', 'net_monthly_return', 'gross_apy', 'net_apy',
               'yield_status', 'average_yield_exposure_usd', 'yield_denominator',
               'spread_reimbursement_excluded_from_net_usd'}
    for old, new in zip(b['allocations'], result['allocations'], strict=True):
        assert {k: v for k, v in old.items() if k not in allowed} == {
            k: v for k, v in new.items() if k not in allowed}
