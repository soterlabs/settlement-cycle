"""First-slice management reporting from immutable saved revenue inputs. No I/O."""
from __future__ import annotations

import base64
import calendar
import json
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext

from .verification import PRIMES, digest

POLICY_VERSION = 'prime-management.first-slice.v1'
LINES = (
    ('demand.agent_rate', 'Agent rate (AR)'),
    ('funding.sky.gross', 'Sky borrowing interest, before reimbursement'),
    ('funding.sky.reimbursement', 'Sky spread reimbursement (deduction)'),
    ('funding.sky.net', 'Sky funding interest, net'),
    ('investment.maple_syrup_usdc', 'Maple syrupUSDC — net investment income'),
)


def metric(value=None, *, reason='not_in_first_slice', basis='unclassified', source_paths=()):
    return {'value': str(value) if value is not None else None, 'known_value': None,
            'unit': 'USD', 'status': 'available' if value is not None else 'unavailable',
            'recognition': 'accrual' if value is not None else 'unknown',
            'basis': basis, 'reason': None if value is not None else reason,
            'source_paths': list(source_paths)}


def number(value):
    if not isinstance(value, str):
        raise ValueError('Expected a saved decimal string')
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('Invalid saved amount') from exc
    if not parsed.is_finite():
        raise ValueError('Non-finite saved amount')
    return parsed


def build(source):
    """Explicit policy: AR/Sky funding for all primes; Obex V1 net vault yield.

    No full interest/expense/revenue/tax totals are certified by this policy.
    Missing inputs affect only the dependent metrics, not their known siblings.
    """
    raw = source['result']
    if source['prime'] not in PRIMES or digest(raw) != source['result_hash']:
        raise ValueError('Invalid source identity/hash')
    cutoff = date.fromisoformat(source['cutoff'])
    if raw.get('prime_id') != source['prime'] or raw.get('period', {}).get('end') != str(cutoff):
        raise ValueError('Source prime/cutoff mismatch')
    if raw['period'].get('start') != str(cutoff.replace(day=1)):
        raise ValueError('Source must cover month to date')
    values = {key: metric(reason='missing_source_input') for key, _ in LINES}
    def saved(key):
        try:
            return number(raw[key])
        except (KeyError, ValueError):
            return None
    ar = saved('agent_rate')
    values['demand.agent_rate'] = metric(ar, reason='missing_source_input',
        basis='tracked_protocol_entitlement', source_paths=['result.agent_rate'])
    sky, sde, rebate = (saved(k) for k in ('sky_revenue', 'sde_revenue', 'susds_spread_reimbursement'))
    with localcontext() as ctx:
        ctx.prec = 60
        if sky is not None and sde is not None:
            net = sky - sde
            values['funding.sky.net'] = metric(net, basis='net_sky_funding_only',
                source_paths=['result.sky_revenue', 'result.sde_revenue'])
            if rebate is not None:
                values['funding.sky.gross'] = metric(net + rebate, basis='sky_borrowing_before_reimbursement',
                    source_paths=['result.sky_revenue', 'result.sde_revenue', 'result.susds_spread_reimbursement'])
        values['funding.sky.reimbursement'] = metric(rebate, reason='missing_source_input',
            basis='sky_funding_reduction', source_paths=['result.susds_spread_reimbursement'])
    portfolio = {'measure': 'tracked_allocations', 'perimeter_id': None, 'positions': [],
                 'expected_position_ids': [], 'excluded_source_venue_ids': [],
                 'opening_value': metric(reason='perimeter_not_in_first_slice'),
                 'closing_value': metric(reason='perimeter_not_in_first_slice')}
    if source['prime'] == 'obex':
        portfolio.update(perimeter_id='obex.maple-only.v1', expected_position_ids=['obex.maple_syrup_usdc'])
        rows = raw.get('venue_breakdown', []) + raw.get('reporting_only_breakdown', raw.get('display_only_breakdown', []))
        matches = [r for r in rows if r.get('venue_id') == 'V1']
        view_rows = raw.get('venue_views', {}).get('reporting', [])
        visible = not any(r.get('venue_id') == 'V1' and not r.get('enabled') for r in view_rows)
        if len(matches) == 1 and visible:
            row = matches[0]
            # V1 is a reviewed, USD-stable ERC-4626 investment. Do not extend
            # this treatment to other venues by category/label matching.
            try:
                if row.get('pricing_category') != 'B' or number(row['sd_share']) != 0:
                    raise ValueError('Unsupported ownership/measurement')
                def saved_row(key):
                    try:
                        return number(row[key])
                    except (KeyError, ValueError):
                        return None
                revenue, external = saved_row('revenue'), saved_row('external_revenue')
                income = None
                if revenue is not None and external is not None:
                    with localcontext() as ctx:
                        ctx.prec = 60
                        income = revenue - external
                values['investment.maple_syrup_usdc'] = metric(income, reason='missing_source_input',
                    basis='prime_owned_net_vault_yield_after_embedded_fees',
                    source_paths=['result.venue_breakdown.V1.revenue', 'result.venue_breakdown.V1.external_revenue'])
                opening = metric(saved_row('value_som'), reason='missing_source_input', basis='reported_usd_vault_valuation')
                closing = metric(saved_row('value_eom'), reason='missing_source_input', basis='reported_usd_vault_valuation')
                for item in (opening, closing):
                    if item['value'] is not None:
                        item['recognition'] = 'valuation'
                portfolio.update(opening_value=opening, closing_value=closing)
                portfolio['positions'] = [{'position_id': 'obex.maple_syrup_usdc', 'source_venue_id': 'V1',
                    'label': 'Maple syrupUSDC', 'opening_value': opening, 'closing_value': closing,
                    'income': values['investment.maple_syrup_usdc']}]
            except (KeyError, ValueError):
                values['investment.maple_syrup_usdc'] = metric(reason='unsupported_maple_measurement')
        else:
            values['investment.maple_syrup_usdc'] = metric(reason='maple_source_missing_or_disabled')
        unknown = sorted({r.get('venue_id') for r in rows + view_rows if r.get('venue_id') not in (None, 'V1')})
        portfolio['excluded_source_venue_ids'] = unknown
        if unknown:
            for key in ('opening_value', 'closing_value'):
                item = portfolio[key]
                portfolio[key] = {**item, 'known_value': item['value'], 'value': None,
                                  'status': 'partial', 'reason': 'unreviewed_positions'}
    else:
        values['investment.maple_syrup_usdc'] = metric(reason='not_applicable_to_pilot_prime')
    return {'schema_version': '3.0', 'policy_version': POLICY_VERSION, 'prime': source['prime'],
            'scope': 'individual', 'perimeter': 'operational_prime_management_reporting',
            'cutoff': str(cutoff), 'period': {'start': raw['period']['start'], 'end': str(cutoff)},
            'source': {k: source.get(k) for k in ('revision_id', 'result_hash', 'code_version',
                'configuration_version', 'input_revision', 'computed_at', 'provisional', 'excluded_inputs')},
            'metrics': values, 'portfolio': portfolio,
            'limitations': ['Not a complete entity income statement', 'Expenses, tax, DR and consolidation are not certified']}


def selection_token(selection):
    return base64.urlsafe_b64encode(json.dumps(selection, sort_keys=True, separators=(',', ':')).encode()).decode().rstrip('=')


def decode_selection(token, prime, year):
    try:
        if len(token) > 4096:
            raise ValueError('Oversized report selection')
        selection = json.loads(base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True))
        if (set(selection) != {'prime', 'year', 'as_of', 'exact', 'revisions', 'policy_version'}
                or selection['prime'] != prime or selection['year'] != year
                or selection['policy_version'] != POLICY_VERSION
                or type(selection['exact']) is not bool or not isinstance(selection['revisions'], list)
                or len(selection['revisions']) != 12):
            raise ValueError('Report selection mismatch')
        date.fromisoformat(selection['as_of'])
        for ref in selection['revisions']:
            if ref is not None and (not isinstance(ref, str) or len(ref) != 64 or any(c not in '0123456789abcdef' for c in ref)):
                raise ValueError('Invalid financial revision')
        return selection
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError('Invalid report revision selection') from exc


def statements(prime, year, as_of, publications, *, exact=False, selection=None):
    """Read projection of saved financial metrics, with immutable export selection."""
    chosen, periods = [], []
    for month in range(1, 13):
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
        future = start > as_of
        partial = start <= as_of < end
        requested = min(end, as_of) if not future else None
        candidates = [p for p in publications if start <= date.fromisoformat(p['cutoff']) <= end]
        if selection is not None:
            ref = selection['revisions'][month - 1]
            item = next((p for p in candidates if p['revision_id'] == ref), None) if ref else None
            if ref and item is None:
                raise ValueError('Retained financial revision unavailable')
        else:
            eligible = [p for p in candidates if not future and (
                date.fromisoformat(p['cutoff']) == requested if (not partial or exact)
                else date.fromisoformat(p['cutoff']) <= requested)]
            item = max(eligible, key=lambda p: (p['cutoff'], p.get('publication_order', 0)), default=None)
        if item and (future or date.fromisoformat(item['cutoff']) > requested
                     or ((not partial or exact) and date.fromisoformat(item['cutoff']) != requested)):
            raise ValueError('Financial revision does not cover the selected period')
        chosen.append(item)
        periods.append({'key': f'{year}-{month:02}', 'state': 'future' if future else 'partial' if partial else 'complete',
                        'expected_cutoff': str(requested) if requested else None,
                        'actual_cutoff': item['cutoff'] if item else None,
                        'financial_revision': item['revision_id'] if item else None,
                        'stale': bool(item and item['cutoff'] < str(requested))})
    manifest = {'prime': prime, 'year': year, 'as_of': str(as_of), 'exact': exact,
                'policy_version': POLICY_VERSION, 'revisions': [p['revision_id'] if p else None for p in chosen]}
    lines = []
    for key, label in LINES:
        cells = [p['document']['metrics'][key] if p else metric(reason='future_period' if periods[i]['state'] == 'future'
                  else 'source_not_published') for i, p in enumerate(chosen)]
        for index, period in enumerate(periods):
            if period['state'] == 'future':
                cells[index] = {**cells[index], 'status': 'future'}
        required = [i for i, p in enumerate(periods) if p['state'] != 'future']
        known = [number(cells[i]['value']) for i in required if cells[i]['status'] == 'available']
        total = metric(reason='incomplete_year_coverage')
        if known:
            with localcontext() as ctx:
                ctx.prec = 60
                amount = sum(known, Decimal(0))
            if len(known) == len(required):
                total = metric(amount, basis='sum_of_selected_monthly_flows')
            else:
                total.update(status='partial', known_value=str(amount))
        lines.append({'id': key, 'label': label, 'values': {p['key']: cells[i] for i, p in enumerate(periods)}, 'total': total})
    result = {'schema_version': '3.0', 'policy_version': POLICY_VERSION, 'scope': 'individual', 'prime': prime,
              'year': year, 'as_of': str(as_of), 'periods': periods, 'lines': lines,
              'total_label': 'FY' if as_of >= date(year, 12, 31) else 'YTD',
              'source_manifest': {p['revision_id']: {'cutoff': p['cutoff'], **p['document']['source']} for p in chosen if p},
              'selection': selection_token(manifest), 'perimeter': 'operational_prime_management_reporting'}
    return {**result, 'report_hash': digest(result)}
