"""Reporting-only AMM returns from immutable MSC sources; no provider calls.

Capital flows and Sky's share are excluded. Issuer/off-pool rewards remain
separate. The legacy v4 valuation omits uncollected/collected fee reconciliation,
so its Spark-retained price return is a known portion, not a complete fee accrual.
"""
import re
from decimal import Decimal, localcontext

from .financial import metric, number

SPECS = (
    ('S61', 'investment.amm.uniswap_v4.pyusd_usds', 'PYUSD / USDS', 'Uniswap v4'),
    ('S62', 'investment.amm.uniswap_v4.usdt_usds', 'USDT / USDS', 'Uniswap v4'),
    ('S66', 'investment.amm.uniswap_v4.rlusd_usds', 'RLUSD / USDS', 'Uniswap v4'),
    ('S24', 'investment.amm.curve.susds_usdt', 'sUSDS / USDT', 'Curve'),
    ('S25', 'investment.amm.curve.pyusd_usds', 'PYUSD / USDS', 'Curve'),
)


def taxonomy(prime):
    return [{'id': key, 'label': label, 'family': family, 'aggregation': 'leaf'}
            for _, key, label, family in SPECS] if prime == 'spark' else []


def money(value):
    if not isinstance(value, str) or not re.fullmatch(r'-?\$(?:\d+|\d{1,3}(?:,\d{3})+)\.\d{2}', value):
        raise ValueError('Invalid published AMM amount')
    return number(value.replace('$', '').replace(',', ''))


def monthly_rows(publication):
    text = publication.get('raw_summary', '')
    if publication.get('prime') != 'spark' or text.count('\n## Per-venue\n') != 1:
        raise ValueError('Missing Spark per-venue summary')
    section = text.split('\n## Per-venue\n')[1].split('\n## ')[0]
    cells = [[c.strip() for c in line.split('|')[1:-1]] for line in section.splitlines() if line.startswith('|')]
    if not cells or not {'Venue','value_som','value_eom','period_inflow','actual_rev','revenue','sd_revenue','sd_share'} <= set(cells[0]):
        raise ValueError('Incomplete AMM summary header')
    return [dict(zip(cells[0], row, strict=True)) for row in cells[2:]]


def project(spec, publication, monthly):
    venue, key, label, family = spec
    unavailable = metric(reason='amm_source_not_published')
    raw = []
    reference = None
    if publication and publication.get('spark_amm_source_hash') == publication['document']['source']['result_hash']:
        if (publication['document'].get('prime') != 'spark' or publication['document'].get('cutoff') != publication['cutoff']):
            return unavailable, None
        matches = [r for r in publication.get('spark_amm_source_rows', []) if r.get('venue_id') == venue]
        views = [r for r in publication.get('spark_fee_source_views', []) if r.get('venue_id') == venue]
        if len(matches) > 1 or len(views) > 1:
            return metric(reason='ambiguous_amm_source'), None
        if len(matches) == 1:
            if matches[0].get('pricing_category') != 'F' or any(not r.get('enabled') or r.get('status') != 'available' for r in views):
                return metric(reason='amm_source_disabled_or_unsupported'), None
            raw, reference = matches, publication
    rounded = False
    if not raw and monthly:
        try:
            matches = [r for r in monthly_rows(monthly) if r.get('Venue') == venue]
        except (KeyError, ValueError):
            return metric(reason='invalid_published_amm_summary'), None
        if len(matches) == 1:
            raw, reference, rounded = matches, monthly, True
    if not raw:
        return unavailable, None
    row = raw[0]
    try:
        with localcontext() as ctx:
            ctx.prec = 60
            read = (lambda name: money(row[name])) if rounded else (lambda name: number(row[name]))
            opening, closing, capital, gross, sky, revenue = (read(k) for k in
                ('value_som','value_eom','period_inflow','actual_rev' if rounded else 'actual_revenue','sd_revenue','revenue'))
            share = number(row['sd_share'].removesuffix('%')) / 100 if rounded else number(row['sd_share'])
            if not 0 <= share <= 1 or min(opening, closing) < 0:
                raise ValueError('Invalid AMM position or share')
            tolerance = Decimal('0.03') if rounded else Decimal('1e-12')
            native = gross - sky
            external = revenue - native if rounded else read('external_revenue')
            if abs(native + external - revenue) > tolerance:
                raise ValueError('Unreconciled native/reward split')
            # S24's saved MSC treatment moves embedded sUSDS appreciation out
            # of SDE-eligible actual_revenue into external_revenue (Case 3b).
            # It is LP value growth, not an off-pool reward. Accept it only
            # when that specific adjustment reconciles the position bridge.
            bridge = closing - opening - capital - gross
            embedded = Decimal(0)
            if venue == 'S24' and abs(bridge) > tolerance and external >= 0 and abs(bridge - external) <= tolerance:
                embedded = external
            if abs(bridge - embedded) > tolerance or abs(gross * share - sky) > tolerance:
                raise ValueError('Unreconciled AMM capital or Sky allocation')
            gross += embedded
            native += embedded
            external -= embedded
    except (KeyError, ValueError, AttributeError):
        return metric(reason='invalid_or_unreconciled_amm_source'), None
    item = metric(native, basis='prime_retained_lp_value_change_less_net_capital_flows_and_sky_share',
                  source_paths=[reference['source']['url'] + '#per-venue' if rounded else f'result.venue_breakdown.{venue}.actual_revenue_minus_sd_revenue'])
    item.update(recognition='investment_return', source_ref=reference['revision_id'], quality='supported',
                period_start=reference['cutoff'][:7] + '-01', period_end=reference['cutoff'],
                allocation_split={'gross_income_usd': str(gross), 'sky_income_usd': str(sky),
                    'prime_income_usd': str(native), 'sky_share': str(share),
                    'sky_share_basis': 'published_rounded_percentage' if rounded else 'saved_source_share',
                    'rounding_delta_usd': '0'})
    if embedded:
        item['allocation_split']['sky_share_excluded_income_usd'] = str(embedded)
    if family == 'Curve' and capital != 0:
        # The retained Curve source prices LP balance changes at the ending
        # unit price rather than each transaction's execution price.
        item['quality'] = 'estimate'
    # Even zero v4 price movement does not establish zero fee income. Under a
    # fixed 100% Sky share the Spark-retained native return is zero either way.
    incomplete = family == 'Uniswap v4' and share != 1
    if incomplete:
        item.update(value=None, known_value=str(native), status='partial', quality='estimate',
                    reason='v4_trading_fee_accrual_not_fully_reconciled')
    ledger = {'venue_id': venue, 'metric_id': key, 'label': label, 'family': family,
              'opening_position_usd': str(opening), 'closing_position_usd': str(closing),
              'net_capital_added_usd': str(capital), 'gross_position_return_usd': str(gross),
              'sky_return_usd': str(sky), 'spark_position_return_usd': str(native),
              'external_rewards_excluded_usd': str(external), 'issuer_incentives_included': False,
              'embedded_susds_return_usd': str(embedded),
              'fee_coverage': 'incomplete_v4_fee_reconciliation' if incomplete else 'retained_msc_curve_valuation' if family == 'Curve' else 'native_return_allocated_to_sky',
              'source_ref': reference['revision_id'], 'through': reference['cutoff'],
              'source_display_precision': '0.01' if rounded else 'full_source_precision'}
    return item, ledger


def extend(statement, chosen, monthly):
    lookup = {r['id']: r for r in statement['lines']}
    statement['spark_amm_ledger'] = []
    for period, publication, summary in zip(statement['periods'], chosen, monthly, strict=True):
        if period['state'] == 'future':
            continue
        for spec in SPECS:
            item, ledger = project(spec, publication, summary)
            if ledger and item['status'] == 'available' and ledger['through'] < period['expected_cutoff']:
                item.update(status='partial', known_value=item['value'], value=None, reason='amm_source_does_not_cover_selected_period')
            lookup[spec[1]]['values'][period['key']] = item
            if ledger:
                statement['spark_amm_ledger'].append({**ledger, 'month': period['key'], 'income': item})
    return statement
