from copy import deepcopy
from datetime import date

import pytest
from fastapi.testclient import TestClient

from settle.api.app import create_app
from settle.revenue.financial import build, decode_selection, number, statements
from settle.revenue.verification import digest


def source(cutoff='2026-10-26', prime='obex', **changes):
    raw = dict(prime_id=prime, period={'start': cutoff[:7] + '-01', 'end': cutoff},
               agent_rate='12.000000000000000001', sky_revenue='130', sde_revenue='30',
               susds_spread_reimbursement='5', monthly_distribution_rewards='0',
               venue_breakdown=[dict(venue_id='V1', pricing_category='B', sd_share='0',
                    revenue='120', external_revenue='20', value_som='1000', value_eom='1100')])
    raw.update(changes)
    return dict(prime=prime, cutoff=cutoff, result=raw, result_hash=digest(raw),
                revision_id=digest(raw), publication_order=1, provisional=True)


def publication(cutoff='2026-10-26', **changes):
    saved = source(cutoff, **changes)
    return dict(cutoff=cutoff, revision_id=digest(saved), publication_order=1, document=build(saved))


def test_explicit_financial_bridge_and_net_maple_policy():
    saved = source()
    before = deepcopy(saved)
    doc = build(saved)
    m = doc['metrics']
    assert m['demand.agent_rate']['value'] == '12.000000000000000001'
    assert m['funding.sky.net']['value'] == '100'
    assert m['funding.sky.gross']['value'] == '105'
    assert m['funding.sky.reimbursement']['value'] == '5'
    assert m['investment.maple_syrup_usdc']['value'] == '100'
    assert doc['portfolio']['closing_value']['value'] == '1100'
    assert doc['portfolio']['expected_position_ids'] == ['obex.maple_syrup_usdc']
    assert 'net_income' not in m and 'monthly_distribution_rewards' not in m
    assert saved == before


def test_missing_and_unreviewed_inputs_do_not_become_zero_or_hide_siblings():
    saved = source(sde_revenue=None)
    doc = build(saved)
    assert doc['metrics']['funding.sky.net']['value'] is None
    assert doc['metrics']['demand.agent_rate']['status'] == 'available'
    rows = saved['result']['venue_breakdown'] + [dict(venue_id='V2')]
    doc = build(source(venue_breakdown=rows))
    assert doc['portfolio']['closing_value']['status'] == 'partial'
    assert doc['portfolio']['closing_value']['value'] is None
    assert doc['portfolio']['closing_value']['known_value'] == '1100'
    assert doc['portfolio']['excluded_source_venue_ids'] == ['V2']
    assert build(source(prime='spark'))['portfolio']['closing_value']['value'] is None
    assert build(source(venue_views={'reporting': [{'venue_id': 'V1', 'enabled': False}]}))['metrics']['investment.maple_syrup_usdc']['value'] is None


@pytest.mark.parametrize('value', [True, 1.2, None, 'NaN', 'Infinity', 'bad'])
def test_invalid_decimal_inputs(value):
    with pytest.raises(ValueError):
        number(value)


def test_source_hash_and_identity_are_verified():
    saved = source()
    saved['result']['agent_rate'] = '99'
    with pytest.raises(ValueError):
        build(saved)
    with pytest.raises(ValueError):
        build(source(period={'start': '2026-09-01', 'end': '2026-10-26'}))


def test_month_end_current_month_and_immutable_selection():
    rows = [publication('2026-09-29'), publication('2026-10-25'), publication()]
    doc = statements('obex', 2026, date(2026, 10, 27), rows)
    line = doc['lines'][0]
    assert line['values']['2026-09']['value'] is None  # near month end is not month end
    assert line['values']['2026-10']['value'] == '12.000000000000000001'
    assert doc['periods'][9]['stale'] is True
    assert line['values']['2026-11']['status'] == 'future'
    assert line['total']['value'] is None and line['total']['known_value'] == '12.000000000000000001'
    selection = decode_selection(doc['selection'], 'obex', 2026)
    corrected = publication('2026-10-27', agent_rate='999')
    pinned = statements('obex', 2026, date(2026, 10, 27), [*rows, corrected, publication('2026-09-30')], selection=selection)
    assert pinned == doc  # newly filled missing slots stay missing in retained reports
    exact = statements('obex', 2026, date(2026, 10, 27), rows, exact=True)
    assert exact['periods'][9]['financial_revision'] is None
    with pytest.raises(ValueError):
        decode_selection(doc['selection'], 'spark', 2026)
    with pytest.raises(ValueError):
        decode_selection('garbage', 'obex', 2026)


def test_leap_month_and_complete_year_total_not_daily_mtd_sum():
    rows = [publication(f'2024-{month:02}-' + ('29' if month == 2 else '30' if month in (4, 6, 9, 11) else '31')) for month in range(1, 13)]
    rows.append(publication('2024-02-28', agent_rate='999'))
    doc = statements('obex', 2024, date(2024, 12, 31), rows)
    assert doc['lines'][0]['total']['value'] == '144.000000000000000012'
    assert doc['total_label'] == 'FY'


def test_read_api_guards_and_etag(monkeypatch):
    from settle.api import revenue
    monkeypatch.setattr(revenue, 'today', lambda: date(2026, 10, 28))
    class Reader:
        def financial_year(self, prime, year, revisions=None):
            return [publication()]
    client = TestClient(create_app(reader=Reader()))
    path = '/v3/reporting/statements?prime=obex&year=2026'
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert client.get(path, headers={'If-None-Match': response.headers['etag']}).status_code == 304
    assert client.get(path + '&scope=group').status_code == 422
    assert client.get(path + '&cutoff=2026-10-28').status_code == 422
    assert client.get(path + '&cutoff=2025-12-31').status_code == 422
    assert client.get(path + '&revision=invalid').status_code == 422
    assert client.get('/v3/reporting/statements?prime=unknown&year=2026').status_code == 404


def test_maple_income_and_valuations_have_independent_availability():
    row = source()['result']['venue_breakdown'][0]
    doc = build(source(venue_breakdown=[{**row, 'external_revenue': None}]))
    assert doc['metrics']['investment.maple_syrup_usdc']['value'] is None
    assert doc['portfolio']['closing_value']['value'] == '1100'
    doc = build(source(venue_breakdown=[{**row, 'value_eom': None}]))
    assert doc['metrics']['investment.maple_syrup_usdc']['value'] == '100'
    assert doc['portfolio']['opening_value']['value'] == '1000'
    assert doc['portfolio']['closing_value']['value'] is None


def test_portfolio_retained_revision_survives_month_rollover(monkeypatch):
    from settle.api import revenue
    monkeypatch.setattr(revenue, 'today', lambda: date(2026, 11, 2))
    p = publication()
    class Reader:
        def financial_bundle(self, prime, cutoff=None, source_revision=None, revision=None):
            assert cutoff is None  # a pinned October MTD report is not silently changed to month end
            return {**p, 'source_revision': p['document']['source']['revision_id'],
                    'document_hash': digest(p['document']), 'computed_at': '2026-10-27T00:00:00Z'}, None
    client = TestClient(create_app(reader=Reader()))
    response = client.get('/v3/reporting/portfolio', params={'prime': 'obex', 'month': '2026-10', 'revision': p['revision_id']})
    assert response.status_code == 200, response.text
    assert response.json()['cutoff'] == '2026-10-26'
