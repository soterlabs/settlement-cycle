from copy import deepcopy
from datetime import date

from settle.revenue.financial import (
    FIRST_SLICE_POLICY,
    POLICY_VERSION,
    build,
    decode_selection,
    statements,
)
from settle.revenue.financial_allocations import ALLOCATIONS
from settle.revenue.verification import digest
from tests.unit.test_financial_reporting import source


def allocation_source(prime='spark', *, changes=None, missing=(), extra=()):
    rows = [dict(venue_id=s.venue, pricing_category=s.category, sd_share='0', revenue='12',
                 external_revenue='2', value_som='100', value_eom='110') for s in ALLOCATIONS[prime]
            if s.venue not in missing]
    if prime == 'osero' and 'O2' not in missing:
        rows.append(dict(venue_id='O2', pricing_category='A', sd_share='0', value_som='5', value_eom='7'))
    for row in rows:
        row.update((changes or {}).get(row['venue_id'], {}))
    rows.extend(extra)
    return source(prime=prime, venue_breakdown=rows, chronicle_points='15.25')


def test_native_yield_rewards_and_migrated_deployments_reconcile_once():
    saved = allocation_source()
    before = deepcopy(saved)
    doc = build(saved)
    m = doc['metrics']
    assert m['investment.sparklend.usds']['value'] == '10'
    assert m['rewards.lending.sparklend.usds']['value'] == '2'
    assert m['rewards.lending.sparklend.usds']['recognition'] == 'receipt'
    assert m['investment.morpho.blue_chip_usdt']['value'] == '20'
    assert m['investment.reviewed_income']['value'] == str(10 * len(ALLOCATIONS['spark']))
    assert m['rewards.reviewed_lending']['value'] == str(2 * sum(s.category == 'C' for s in ALLOCATIONS['spark']))
    positions = doc['portfolio']['positions']
    assert len({p['position_id'] for p in positions}) == len(positions)
    assert {p['source_venue_id'] for p in positions} >= {'S11', 'S65'}
    assert saved == before


def test_unknown_savings_claim_cash_receipts_and_relays_never_enter_approved_subtotal():
    extra = [dict(venue_id=v, pricing_category=kind, sd_share='0', revenue='999999', external_revenue='0',
                  value_som='999999', value_eom='999999') for v, kind in
             [('S23', 'E'), ('S26', 'A'), ('S28', 'A'), ('S56', 'S2'), ('S67', 'INCENTIVE'), ('S68', 'INCENTIVE')]]
    doc = build(allocation_source(extra=extra))
    assert doc['metrics']['investment.reviewed_income']['value'] == '250'
    portfolio = doc['portfolio']
    assert portfolio['closing_value']['value'] is None
    assert portfolio['closing_value']['known_value'] == '2750'
    assert set(portfolio['excluded_source_venue_ids']) == {e['venue_id'] for e in extra}


def test_missing_disabled_duplicate_or_sde_sources_do_not_hide_known_siblings_or_invent_zero():
    for saved in [allocation_source(missing=['S65']),
                  allocation_source(changes={'S65': {'sd_share': '1'}}),
                  allocation_source(extra=[dict(venue_id='S65')])]:
        doc = build(saved)
        assert doc['metrics']['investment.morpho.blue_chip_usdt']['value'] is None
        assert doc['metrics']['investment.morpho.blue_chip_usdt']['known_value'] == '10'
        assert doc['metrics']['investment.sparklend.usds']['value'] == '10'
        assert doc['metrics']['investment.reviewed_income']['status'] == 'partial'
    saved = allocation_source()
    saved['result']['venue_views'] = {'reporting': [dict(venue_id='S1', enabled=False, status='disabled')]}
    saved['result_hash'] = digest(saved['result'])
    assert build(saved)['metrics']['investment.sparklend.usds']['value'] is None


def test_signed_amounts_zero_and_income_valuation_independence():
    saved = allocation_source(changes={'S1': {'revenue': '-3', 'external_revenue': '2', 'value_eom': None},
                                     'S2': {'revenue': '0', 'external_revenue': '0'}})
    doc = build(saved)
    assert doc['metrics']['investment.sparklend.usds']['value'] == '-5'
    assert doc['metrics']['investment.sparklend.usdc']['value'] == '0'
    assert doc['portfolio']['closing_value']['value'] is None
    assert doc['metrics']['investment.reviewed_income']['value'] is not None


def test_osero_complete_perimeter_includes_only_disjoint_cash_and_lending_positions():
    doc = build(allocation_source('osero'))
    assert doc['portfolio']['closing_value']['value'] == '117'
    assert doc['portfolio']['expected_position_ids'] == ['osero.O1', 'osero.O2']
    assert doc['metrics']['investment.reviewed_income']['value'] == '10'
    assert doc['portfolio']['positions'][1]['income']['value'] is None
    assert build(allocation_source('osero', missing=['O2']))['portfolio']['closing_value']['value'] is None


def test_dated_policy_and_grove_entitlement_are_explicit():
    doc = build(allocation_source('grove'))
    assert doc['metrics']['demand.chronicle_points']['value'] == '15.25'
    raw = allocation_source()
    raw['cutoff'] = raw['result']['period']['end'] = '2026-08-31'
    raw['result']['period']['start'] = '2026-08-01'
    raw['result_hash'] = digest(raw['result'])
    assert build(raw)['metrics']['investment.sparklend.usds']['reason'] == 'policy_before_effective_date'


def test_retained_v1_document_and_annual_report_are_unchanged_by_new_policy():
    from tests.unit.test_financial_reporting import publication
    saved = source()
    old = build(saved, policy_version=FIRST_SLICE_POLICY)
    assert digest(old) == '96911f5e15ca982c9c45e357533e034b064c546140e28951f06d941f7e73b5c3'
    pub = publication()
    pub.update(document=old)
    old_selection = {'prime': 'obex', 'year': 2026, 'as_of': '2026-10-27', 'exact': False,
                     'policy_version': FIRST_SLICE_POLICY, 'revisions': [None] * 9 + [pub['revision_id']] + [None] * 2}
    original = statements('obex', 2026, date(2026, 10, 27), [pub], selection=old_selection)
    selected = decode_selection(original['selection'], 'obex', 2026)
    replayed = statements('obex', 2026, date(2026, 10, 27), [pub], selection=selected)
    assert replayed == original
    assert original['policy_version'] == FIRST_SLICE_POLICY
    assert len(original['lines']) == 5
    assert build(saved)['policy_version'] == POLICY_VERSION


def issuer_source(program='pyusd', *, closing='-30', paid='50', accrued='20'):
    from settle.revenue.financial_issuers import ISSUERS
    venue, _, _, payer, recipient, token = next(s for s in ISSUERS if s[1] == program)
    row = dict(venue_id=venue, pricing_category='I', settlement_enabled=False,
               revenue=accrued, value_som='0', value_eom=closing, sd_share='0', external_revenue='0',
               incentive_accrual={'program': program, 'cutoff': '2026-10-26', 'period_start': '2026-10-01',
                    'period_opening_balance': '0', 'period_accrual': accrued,
                    'period_receipts': paid, 'accrued_balance': closing,
                    'receipt_sources': [dict(payer=payer, recipient=recipient, token=token, chain='robinhood' if program == 'usdg' else 'ethereum')]})
    saved = allocation_source(extra=[row])
    return saved


def test_issuer_accrual_does_not_double_count_raw_receipts_and_prepaid_claim_is_separate():
    doc = build(issuer_source())
    assert doc['metrics']['rewards.issuer.pyusd']['value'] == '20'
    claim = next(c for c in doc['claims'] if c['source_venue_id'] == 'S68')
    assert claim['cash_received']['value'] == '50'
    assert claim['receivable']['value'] == '0'
    assert claim['received_in_advance']['value'] == '30'
    assert claim['included_in_allocation_value'] is False
    assert doc['metrics']['investment.reviewed_income']['value'] == '250'
    assert build(allocation_source())['metrics']['rewards.issuer.pyusd']['value'] is None


def test_unreconciled_or_wrongly_attributed_issuer_ledgers_are_not_published_as_income():
    from settle.revenue.verification import digest
    for field, value in [('accrued_balance', '999'), ('period_start', '2026-09-01'), ('program', 'rlusd'),
                         ('receipt_sources', [dict(payer='unreviewed')])]:
        saved = issuer_source()
        saved['result']['venue_breakdown'][-1]['incentive_accrual'][field] = value
        saved['result_hash'] = digest(saved['result'])
        assert build(saved)['metrics']['rewards.issuer.pyusd']['value'] is None
    assert build(issuer_source('usdg', closing='0', paid='50', accrued='50'))['metrics']['rewards.issuer.usdg']['recognition'] == 'receipt'
    assert build(issuer_source('usdg', closing='0', paid='50', accrued='60'))['metrics']['rewards.issuer.usdg']['value'] is None


def test_malformed_optional_issuer_data_does_not_hide_known_investment_siblings():
    for ledger in (None, 'unavailable', {'receipt_sources': [None], 'program': 'pyusd',
                    'cutoff': '2026-10-26', 'period_start': '2026-10-01'}):
        saved = issuer_source()
        saved['result']['venue_breakdown'][-1]['incentive_accrual'] = ledger
        saved['result_hash'] = digest(saved['result'])
        doc = build(saved)
        assert doc['metrics']['rewards.issuer.pyusd']['value'] is None
        assert doc['metrics']['investment.reviewed_income']['value'] == '250'
