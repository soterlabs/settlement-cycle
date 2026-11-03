import csv
import hashlib
import io
import json
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from settle.revenue.financial import ARCHIVE_POLICY, OFFCHAIN_POLICY, decode_selection, statements
from settle.revenue.financial_archive import SNAPSHOTS, parse, publications
from settle.revenue.verification import digest

ROOT = Path(__file__).parents[2]


def saved():
    return json.loads((ROOT / 'tests/fixtures/grove_rwa_financial_2026_10.json').read_text())


def report(**kwargs):
    return statements('grove', 2026, date(2026, 11, 2), [saved()], archive=True, **kwargs)


def line(r, key):
    return next(row for row in r['lines'] if row['id'] == key)


def amount(item):
    return Decimal(item['value'] or item['known_value'] or '0')


def test_all_supplied_income_and_reference_rows_are_exposed_once():
    r = report()
    assert r['policy_version'] == ARCHIVE_POLICY
    assert len(r['archive_income']) == 34
    assert sum(row['included_in_totals'] for row in r['archive_income']) == 22
    assert len({(row['month'], row['id']) for row in r['archive_income']}) == 34
    assert line(r, 'rewards.issuer.agora')['values']['2026-03']['value'] == '561262.52'
    assert line(r, 'rewards.issuer.agora')['values']['2026-01']['value'] == '0'
    assert line(r, 'rewards.issuer.agora')['values']['2026-10']['value'] is None
    assert line(r, 'rewards.issuer.usdg')['values']['2026-08']['value'] == '30355.272510'
    assert line(r, 'rewards.issuer.usdg')['values']['2026-09']['value'] == '38112.201038'
    assert line(r, 'rewards.partnership.plume')['values']['2026-04']['value'] == '753649'
    assert line(r, 'gains.plume_disposal')['values']['2026-06']['value'] == '1102056.359999'
    assert line(r, 'statement.gains')['values']['2026-06']['known_value'] == '1102056.359999'


def test_monad_capital_flows_and_valuation_are_not_extra_income():
    r = report()
    rows = [row for row in r['archive_income'] if row['id'] == 'monad']
    assert len(rows) == 4
    with localcontext() as ctx:
        ctx.prec = 100
        for row in rows:
            m, b = row['metric'], row['balances']
            assert abs(amount(b['closing_value']) - amount(b['opening_value']) - amount(b['deposits']) + amount(b['withdrawals']) - amount(m)) < Decimal('1e-18')
            assert m['quality'] == 'estimate' and m['source_currency'] == 'AUSD'
            assert m['recognition'] == 'investment_return'
    april = next(row for row in rows if row['month'] == '2026-04')
    assert april['balances']['closing_value']['value'] == '0'
    assert april['metric']['value'] == '16287.687147596073832732012042285546608'


def test_merkl_settlements_exclude_interest_mints_and_never_imply_cash_or_complete_earnings():
    r = report()
    rows = [row for row in r['archive_income'] if row['id'] in ('merkl_core', 'merkl_horizon')]
    assert len(rows) == 5
    core = line(r, 'rewards.lending.aave.eth_rlusd')
    assert core['values']['2026-02']['known_value'] == '2963561.635045723390973932'
    for row in rows:
        assert row['metric']['recognition'] == 'receipt_in_kind'
        assert row['metric']['status'] == 'partial' and row['metric']['quality'] == 'estimate'
        assert row['metric']['conversion'] == 'supplied_unit_price_1_USD'
    # Unobserved claims and incomplete recorded zeroes never become confirmed zero income.
    assert core['values']['2026-01']['value'] is None
    assert core['values']['2026-05']['value'] is None
    assert not any(row['id'].endswith('_recorded') and amount(row['metric']) == 0 for row in r['archive_income'])


def test_merkl_scenarios_and_recorded_changes_are_never_statement_addends():
    r = report()
    refs = [row for row in r['archive_income'] if not row['included_in_totals']]
    assert len(refs) == 12
    assert all(row['line_id'] is None for row in refs)
    scenarios = [row for row in refs if row['id'].endswith('_scenario')]
    assert {row['month'] for row in scenarios} == {'2026-04', '2026-05', '2026-06'}
    assert all(row['metric']['recognition'] == 'estimated_accrual' for row in scenarios)
    assert not any('scenario' in row['id'] or 'recorded' in row['id'] for row in r['lines'])


def test_signed_rollups_add_imported_flows_once_and_do_not_mix_in_reference_figures():
    old = statements('grove', 2026, date(2026, 11, 2), [saved()], offchain=True)
    new = report()
    with localcontext() as ctx:
        ctx.prec = 60
        for month in [f'2026-{i:02}' for i in range(1, 12)]:
            included = [row for row in new['archive_income'] if row['month'] == month and row['included_in_totals']]
            new_total = sum((amount(row['metric']) for row in included), Decimal(0))
            # Imported leaves replace any overlapping existing financial leaf.
            prior = sum((amount(line(old, row['line_id'])['values'][month])
                         for row in included if any(old_line['id'] == row['line_id'] for old_line in old['lines'])), Decimal(0))
            for key in ('statement.net_revenue', 'statement.net_income'):
                delta = amount(line(new, key)['values'][month]) - amount(line(old, key)['values'][month])
                assert abs(delta - (new_total - prior)) < Decimal('1e-40')
    # The token sale is a noninterest gain; partnership receipts are other rewards.
    assert 'gains.plume_disposal' not in [c['id'] for c in line(new, 'statement.interest')['components']]
    assert 'rewards.partnership.plume' in [c['id'] for c in line(new, 'statement.rewards')['components']]


def test_new_and_old_selections_replay_without_changing_offchain_history():
    r = report()
    selection = decode_selection(r['selection'], 'grove', 2026)
    assert report(selection=selection) == r
    old = statements('grove', 2026, date(2026, 11, 2), [saved()], offchain=True)
    old_selection = decode_selection(old['selection'], 'grove', 2026)
    assert report(selection=old_selection) == old
    assert old['policy_version'] == OFFCHAIN_POLICY
    selection['offchain_revisions'][0] = None
    absent = report(selection=selection)
    assert line(absent, 'rewards.issuer.agora')['values']['2026-01']['value'] is None
    assert not any(row['month'] == '2026-01' for row in absent['archive_income'])
    assert len(r['selection']) < 4096


def test_no_future_extrapolation_or_cross_prime_leakage():
    early = statements('grove', 2026, date(2026, 4, 15), [], archive=True)
    assert not any(row['month'] >= '2026-04' for row in early['archive_income'])
    assert all(line(early, key)['values']['2026-04']['value'] is None for key in ['rewards.partnership.plume', 'rewards.issuer.agora'])
    for prime in ('spark', 'obex', 'keel', 'skybase', 'osero'):
        r = statements(prime, 2026, date(2026, 11, 2), [], archive=True)
        assert r['archive_income'] == [] and r['offchain_positions'] == []


def mutate(snapshot, filename, field, value):
    source = next(s for s in snapshot['sources'] if s['path'] == 'data/' + filename)
    reader = csv.DictReader(io.StringIO(source['text']))
    rows = list(reader)
    # Monad includes a December2025 row; mutate first in-scope month.
    index = 1 if filename == 'monad-vault-monthly.csv' else 0
    rows[index][field] = value
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=reader.fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    source.update(text=out.getvalue(), sha256=hashlib.sha256(out.getvalue().encode()).hexdigest())


@pytest.mark.parametrize('filename,field,value', [
    ('monad-vault-monthly.csv', 'withdrawnAusd', '999'),
    ('monad-vault-monthly.csv', 'vaultReturnAusd', 'NaN'),
    ('agora-monthly-cash.csv', 'apiUSD', '999'),
    ('agora-monthly-cash.csv', 'accruedEntitlementUSD', '1000'),
    ('usdg-monthly-rewards.csv', 'currency', 'EUR'),
    ('merkl-claims.csv', 'beneficiary', 'other'),
    ('merkl-claims-coverage.csv', 'historyEndpointMatched', 'False'),
    ('merkl-current-cumulative-tie.csv', 'unexplainedClaimedNativeExact', '1'),
    ('merkl-valuation-policy-evidence.csv', 'observedPriceUsd', '2'),
])
def test_invalid_amounts_identity_proofs_and_conversion_fail_closed(filename, field, value):
    snapshot = json.loads((ROOT / 'src/settle/revenue/published_archive' / (SNAPSHOTS[0] + '.json')).read_text())
    mutate(snapshot, filename, field, value)
    with pytest.raises(ValueError):
        parse(snapshot)


def test_each_publication_has_immutable_provenance_and_original_source_hashes():
    retained, active = publications()
    assert len(active) == 10
    for ref in active.values():
        source = deepcopy(retained[ref]['source'])
        assert source.pop('revision_id') == ref
        assert digest(source) == ref
    january = retained[active[('grove', '2026-01')]]
    assert any(s['path'] == 'data/gaclo-monthly.csv' for s in january['source']['files'])
    assert all(row['metric']['source_ref'] == january['revision_id'] for row in january['archive_income'])
