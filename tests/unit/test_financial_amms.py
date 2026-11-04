from copy import deepcopy
from datetime import date
from decimal import Decimal

from settle.revenue.financial import decode_selection, statements
from settle.revenue.financial_amms import SPECS, project
from settle.revenue.monthly_reports import select
from tests.unit.test_financial_spark import saved


def source():
    row = saved()
    row.update(spark_amm_source_hash=row['document']['source']['result_hash'],
        spark_amm_source_rows=[dict(venue_id='S61', pricing_category='F', value_som='100',
            value_eom='250', period_inflow='140', actual_revenue='10', sd_revenue='0',
            sd_share='0', revenue='13', external_revenue='3')], spark_fee_source_views=[])
    return row


def test_capital_and_external_rewards_never_become_amm_income():
    item, ledger = project(SPECS[0], source(), None)
    assert item['status'] == 'partial' and item['known_value'] == '10'
    assert item['quality'] == 'estimate'
    assert ledger['external_rewards_excluded_usd'] == '3'
    assert ledger['net_capital_added_usd'] == '140'
    assert not ledger['issuer_incentives_included']
    assert item['allocation_split']['prime_income_usd'] == '10'


def test_monthly_history_retains_sky_split_and_signed_returns_without_daily_replay():
    monthly = select('spark', 2026, date(2026,9,30))[8]
    pyusd, _ = project(SPECS[0], None, monthly)
    usdt, ledger = project(SPECS[1], None, monthly)
    rlusd, _ = project(SPECS[2], None, monthly)
    curve, _ = project(SPECS[4], None, monthly)
    assert Decimal(pyusd['known_value']) == Decimal('402.03')
    assert Decimal(rlusd['known_value']) == Decimal('-90.03')
    assert Decimal(usdt['value']) == 0 and usdt['status'] == 'available'
    assert Decimal(ledger['gross_position_return_usd']) == Decimal('3875.84')
    assert ledger['gross_position_return_usd'] == ledger['sky_return_usd']
    assert curve['status'] == 'available' and Decimal(curve['value']) == 0


def test_invalid_capital_bridge_or_disabled_source_never_looks_like_available_income():
    row = source()
    row['spark_amm_source_rows'][0]['value_eom'] = '999'
    assert project(SPECS[0], row, None)[0]['status'] == 'unavailable'
    row = source()
    row['spark_fee_source_views'] = [dict(venue_id='S61',enabled=False,status='disabled')]
    assert project(SPECS[0], row, None)[0]['status'] == 'unavailable'


def test_new_policy_adds_amm_returns_once_and_replays_old_policy_unchanged():
    row = source()
    before = deepcopy(row)
    old = statements('spark',2026,date(2026,10,27),[row],spark_income=True)
    new = statements('spark',2026,date(2026,10,27),[row],spark_amms=True)
    def amount(doc):
        return next(r for r in doc['lines'] if r['id']=='statement.interest')['values']['2026-10']['known_value']
    assert Decimal(amount(new)) - Decimal(amount(old)) == 10
    pinned = decode_selection(old['selection'],'spark',2026)
    assert statements('spark',2026,date(2026,10,27),[row],selection=pinned,spark_amms=True) == old
    assert row == before


def test_historical_policy_works_with_only_retained_monthly_reports():
    new=statements('spark',2026,date(2026,9,30),[],spark_amms=True)
    metric=next(r for r in new['lines'] if r['id']==SPECS[0][1])['values']['2026-09']
    assert metric['status']=='partial' and metric['known_value']=='402.03'
    assert metric['source_ref']==new['periods'][8]['monthly_revision']


def test_ambiguous_exact_source_cannot_fall_back_to_a_monthly_number():
    row = source()
    row['spark_amm_source_rows'] *= 2
    monthly = select('spark', 2026, date(2026,9,30))[8]
    item, ledger = project(SPECS[0], row, monthly)
    assert item['status'] == 'unavailable'
    assert item['reason'] == 'ambiguous_amm_source'
    assert ledger is None


def test_curve_embedded_susds_yield_is_lp_revenue_not_an_external_reward():
    row = source()
    row['spark_amm_source_rows'][0].update(venue_id='S24', value_eom='253',
        sd_share='1', sd_revenue='10', revenue='3', external_revenue='3')
    item, ledger = project(SPECS[3], row, None)
    assert item['value'] == '3' and item['status'] == 'available'
    assert ledger['gross_position_return_usd'] == '13'
    assert ledger['external_rewards_excluded_usd'] == '0'
    assert item['allocation_split']['sky_share_excluded_income_usd'] == '3'
    # An unexplained bridge cannot be interpreted as embedded yield.
    row['spark_amm_source_rows'][0]['value_eom'] = '254'
    assert project(SPECS[3], row, None)[0]['status'] == 'unavailable'
    monthly = select('spark', 2026, date(2026,9,30))[0]
    item, ledger = project(SPECS[3], None, monthly)
    assert item['value'] == '25473.95'
    assert ledger['embedded_susds_return_usd'] == '25473.95'
