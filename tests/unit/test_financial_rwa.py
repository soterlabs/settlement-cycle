import json
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from settle.revenue.financial import RWA_POLICY, decode_selection, statements
from settle.revenue.financial_rwa import SPECS, daily, monthly
from settle.revenue.monthly_reports import publications

FIXTURE = Path(__file__).parents[1] / 'fixtures/grove_rwa_financial_2026_10.json'


def publication():
    return json.loads(FIXTURE.read_text())


def line(report, key):
    return next(row for row in report['lines'] if row['id'] == key)


def test_full_january_to_current_coverage_and_real_ytd_values():
    report = statements('grove', 2026, date(2026, 10, 28), [publication()], rwa=True)
    assert report['policy_version'] == RWA_POLICY
    assert report['periods'][9]['actual_cutoff'] == '2026-10-27'
    assert report['periods'][9]['stale'] is True  # Do not pretend the source covers Oct 28.
    expected = {'investment.rwa.stac': '3753663.200000',
                'investment.rwa.jaaa': '4619674.2646168581493691352',
                'statement.family.rwa_funds': '8373337.4646168581493691352'}
    for key, total in expected.items():
        row = line(report, key)
        assert row['total']['value'] == total
        assert row['total']['status'] == 'available'
        for month in range(1, 11):
            assert row['values'][f'2026-{month:02}']['status'] == 'available'
        for month in (11, 12):
            assert row['values'][f'2026-{month}']['status'] == 'future'
    assert line(report, 'investment.rwa.stac')['values']['2026-10']['recognition'] == 'investment_return'
    assert line(report, 'statement.interest')['values']['2026-10']['known_value'] == '1109542.5120614898257495338262500'
    assert line(report, 'statement.interest')['total']['value'] is None


def test_month_specific_sky_split_and_real_zero_are_preserved():
    retained, active = publications()
    january = monthly(retained[active[('grove', '2026-01')]])['investment.rwa.jaaa']
    assert january['value'] == '933299.18'
    assert january['allocation_split']['gross_income_usd'] == '2363169.37'
    assert january['allocation_split']['sky_income_usd'] == '1429870.18'
    assert january['allocation_split']['sky_share'] == '0.6051'
    march = monthly(retained[active[('grove', '2026-03')]])['investment.rwa.jaaa']
    assert march['status'] == 'available' and march['value'] == '0.00'
    assert march['allocation_split']['gross_income_usd'] == '-477414.38'
    assert Decimal(march['allocation_split']['sky_share']) == 1


def test_both_historical_table_layouts_and_all_eighteen_closed_cells():
    retained, active = publications()
    for month in range(1, 10):
        doc = retained[active[('grove', f'2026-{month:02}')]]
        values = monthly(doc)
        assert len(values) == 2
        for cell in values.values():
            assert cell['value'] is not None
            assert cell['source_ref'] == doc['revision_id']
    assert monthly(retained[active[('grove', '2026-08')]])['investment.rwa.stac']['value'] == '469275.10'


@pytest.mark.parametrize('change', [
    lambda pub: pub.update(rwa_source_hash='0' * 64),
    lambda pub: pub['rwa_source_rows'].append(deepcopy(pub['rwa_source_rows'][0])),
    lambda pub: pub['rwa_source_rows'][0].update(external_revenue='1'),
    lambda pub: pub['rwa_source_rows'][0].update(pricing_category='B'),
    lambda pub: pub['rwa_source_rows'][0].update(label='Different fund'),
    lambda pub: pub['rwa_source_rows'][0].update(revenue='NaN'),
    lambda pub: pub['rwa_source_rows'][0].update(sd_revenue='100'),
    lambda pub: pub['rwa_source_views'][0].update(enabled=False),
])
def test_raw_source_guards_never_invent_income(change):
    pub = publication()
    change(pub)
    assert daily(pub)['investment.rwa.stac']['value'] is None


@pytest.mark.parametrize('before,after', [
    ('| E7 |', '| E77 |'),
    ('Securitize Tokenized AAA CLO Fund (STAC)', 'Another fund'),
    ('| $616,387.90 | $616,387.90 | $0.00 |', '| $616,388.90 | $616,387.90 | $0.00 |'),
    ('60.51%', '160.51%'),
])
def test_invalid_monthly_sources_fail_closed_without_hiding_the_other_fund(before, after):
    retained, active = publications()
    doc = deepcopy(retained[active[('grove', '2026-01')]])
    assert before in doc['raw_summary']
    doc['raw_summary'] = doc['raw_summary'].replace(before, after)
    actual = monthly(doc)
    invalid = 'investment.rwa.jaaa' if before == '60.51%' else 'investment.rwa.stac'
    sibling = 'investment.rwa.stac' if invalid.endswith('jaaa') else 'investment.rwa.jaaa'
    assert actual[invalid]['value'] is None
    assert actual[invalid]['reason'] == 'invalid_or_incomplete_rwa_source'
    assert actual[sibling]['value'] == monthly(retained[active[('grove', '2026-01')]])[sibling]['value']


def test_unrelated_bad_rows_do_not_hide_the_reviewed_funds():
    retained, active = publications()
    doc = deepcopy(retained[active[('grove', '2026-01')]])
    expected = monthly(doc)
    # A malformed, unrelated row is outside the two explicitly reviewed funds.
    doc['raw_summary'] = doc['raw_summary'].replace('| E1 |', '| E1 | extra cell |', 1)
    assert monthly(doc) == expected


def test_bad_published_fund_row_does_not_turn_the_annual_api_into_a_404(monkeypatch):
    import hashlib
    from importlib.resources import files

    from fastapi.testclient import TestClient

    from settle.api import revenue
    from settle.api.app import create_app
    from settle.revenue import monthly_reports
    snapshot = json.loads(files('settle.revenue').joinpath('published_monthly', monthly_reports.SNAPSHOTS[0] + '.json').read_text())
    raw = deepcopy(next(row for row in snapshot['reports'] if row['path'] == 'reports/grove/2026-01/summary.md'))
    raw['text'] = raw['text'].replace('| $616,387.90 | $616,387.90 | $0.00 |',
                                    '| $616,388.90 | $616,387.90 | $0.00 |')
    payload = raw['text'].encode()
    raw['sha256'] = hashlib.sha256(payload).hexdigest()
    raw['git_blob_sha'] = hashlib.sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest()
    changed = monthly_reports.parse({**snapshot, 'commit': 'a' * 40}, raw)
    retained, active = monthly_reports.publications()
    monkeypatch.setattr(monthly_reports, 'publications', lambda: (
        {**retained, changed['revision_id']: changed}, {**active, ('grove', '2026-01'): changed['revision_id']}))
    monkeypatch.setattr(revenue, 'today', lambda: date(2026, 10, 29))
    class Reader:
        def financial_year(self, *args, **kwargs):
            return []
    response = TestClient(create_app(reader=Reader())).get('/v3/reporting/statements?prime=grove&year=2026')
    assert response.status_code == 200, response.text
    report = response.json()
    assert line(report, 'investment.rwa.stac')['values']['2026-01']['value'] is None
    assert line(report, 'investment.rwa.jaaa')['values']['2026-01']['value'] == '933299.18'
    assert line(report, 'demand.agent_rate')['values']['2026-01']['status'] == 'available'
    assert line(report, 'statement.family.rwa_funds')['total']['status'] == 'partial'


def test_old_selections_remain_exact_and_new_sources_stay_pinned():
    pub = publication()
    old = statements('grove', 2026, date(2026, 10, 28), [pub], rollups=True)
    new = statements('grove', 2026, date(2026, 10, 28), [pub], rwa=True)
    assert statements('grove', 2026, date(2026, 10, 28), [pub],
        selection=decode_selection(old['selection'], 'grove', 2026), rwa=True) == old
    assert statements('grove', 2026, date(2026, 10, 28), [pub],
        selection=decode_selection(new['selection'], 'grove', 2026)) == new
    assert not any(row['id'].startswith('investment.rwa.') for row in old['lines'])
    exact = statements('grove', 2026, date(2026, 10, 28), [pub], exact=True, rwa=True)
    assert line(exact, 'investment.rwa.stac')['values']['2026-10']['value'] is None


def test_no_lookahead_and_reviewed_aggregate_counts_rwa_once():
    doc = statements('grove', 2026, date(2026, 8, 15), [], rwa=True)
    assert line(doc, 'investment.rwa.stac')['values']['2026-08']['value'] is None
    full = statements('grove', 2026, date(2026, 10, 28), [publication()], rwa=True)
    reviewed = line(full, 'investment.reviewed_income')['values']['2026-10']
    assert reviewed['value'] == '1109542.5120614898257495338262500'
    assert len(reviewed['source_refs']) == 1
    assert not any(term['id'] == 'investment.reviewed_income'
                   for term in line(full, 'statement.interest')['components'])
    assert len([row for row in full['lines'] if row['id'].startswith('investment.rwa.')]) == len(SPECS)
    for row in full['lines']:
        if row['id'].startswith('investment.rwa.'):
            assert Decimal(row['total']['value']) > 0
