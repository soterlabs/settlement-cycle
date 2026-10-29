"""Versioned management reporting from immutable saved revenue inputs. No I/O."""
from __future__ import annotations

import base64
import calendar
import json
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext

from .verification import PRIMES, digest

FIRST_SLICE_POLICY = 'prime-management.first-slice.v1'
ALLOCATION_V2_POLICY = 'prime-management.allocations.v2'
POLICY_VERSION = 'prime-management.allocations.v2.1'
ALLOCATION_POLICIES = (ALLOCATION_V2_POLICY, POLICY_VERSION)
SUPPORTED_POLICIES = (FIRST_SLICE_POLICY, *ALLOCATION_POLICIES)
STATEMENT_POLICY = 'prime-management.monthly-statements.v3'
ROLLUP_POLICY = 'prime-management.statement-rollups.v3.1'
MONTHLY_STATEMENT_POLICIES = (STATEMENT_POLICY, ROLLUP_POLICY)
# Frozen dependency of this composition policy; do not follow future daily upgrades.
STATEMENT_SOURCE_POLICY = 'prime-management.allocations.v2.1'
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


def _build_first_slice(source):
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
    return {'schema_version': '3.0', 'policy_version': FIRST_SLICE_POLICY, 'prime': source['prime'],
            'scope': 'individual', 'perimeter': 'operational_prime_management_reporting',
            'cutoff': str(cutoff), 'period': {'start': raw['period']['start'], 'end': str(cutoff)},
            'source': {k: source.get(k) for k in ('revision_id', 'result_hash', 'code_version',
                'configuration_version', 'input_revision', 'computed_at', 'provisional', 'excluded_inputs')},
            'metrics': values, 'portfolio': portfolio,
            'limitations': ['Not a complete entity income statement', 'Expenses, tax, DR and consolidation are not certified']}


def build(source, *, policy_version=POLICY_VERSION):
    if policy_version not in SUPPORTED_POLICIES:
        raise ValueError('Unknown financial policy')
    document = _build_first_slice(source)
    if policy_version == FIRST_SLICE_POLICY:
        return document
    from .financial_allocations import extend
    document['policy_version'] = policy_version
    from .financial_issuers import extend as issuer_extend
    return issuer_extend(extend(document, source), source, legacy_paths=policy_version == ALLOCATION_V2_POLICY)


def taxonomy(prime, policy_version):
    if policy_version == STATEMENT_POLICY:
        additions = [('demand.distribution_rewards', 'Distribution rewards (DR)'),
                     ('demand.total', 'Demand-side revenue')]
        if prime == 'skybase':
            additions.append(('demand.gar', 'Governance accessibility rewards (GAR)'))
        return taxonomy(prime, STATEMENT_SOURCE_POLICY) + [{'id': key, 'label': label} for key, label in additions]
    lines = {key: {'id': key, 'label': label} for key, label in LINES}
    if policy_version in ALLOCATION_POLICIES:
        from .financial_allocations import taxonomy as allocation_taxonomy
        from .financial_issuers import taxonomy as issuer_taxonomy
        lines.update({line['id']: line for line in allocation_taxonomy(prime) + issuer_taxonomy(prime)})
    elif policy_version != FIRST_SLICE_POLICY:
        raise ValueError('Unknown financial policy')
    return list(lines.values())


def selection_token(selection):
    return base64.urlsafe_b64encode(json.dumps(selection, sort_keys=True, separators=(',', ':')).encode()).decode().rstrip('=')


def decode_selection(token, prime, year):
    try:
        if len(token) > 4096:
            raise ValueError('Oversized report selection')
        selection = json.loads(base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True))
        if not isinstance(selection, dict):
            raise ValueError('Invalid report selection')
        monthly = selection.get('policy_version') in MONTHLY_STATEMENT_POLICIES
        keys = {'prime', 'year', 'as_of', 'exact', 'revisions', 'policy_version'}
        if monthly:
            keys.add('monthly_revisions')
        if (set(selection) != keys
                or selection['prime'] != prime or selection['year'] != year
                or selection['policy_version'] not in (*SUPPORTED_POLICIES, *MONTHLY_STATEMENT_POLICIES)
                or type(selection['exact']) is not bool or not isinstance(selection['revisions'], list)
                or len(selection['revisions']) != 12):
            raise ValueError('Report selection mismatch')
        date.fromisoformat(selection['as_of'])
        monthly_refs = selection.get('monthly_revisions', [])
        if monthly and (not isinstance(monthly_refs, list) or len(monthly_refs) != 12):
            raise ValueError('Invalid monthly revision selection')
        for ref in selection['revisions'] + monthly_refs:
            if ref is not None and (not isinstance(ref, str) or len(ref) != 64 or any(c not in '0123456789abcdef' for c in ref)):
                raise ValueError('Invalid financial revision')
        return selection
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError('Invalid report revision selection') from exc


def statements(prime, year, as_of, publications, *, exact=False, selection=None, monthly=False, rollups=False):
    """Read projection of saved financial metrics, with immutable export selection."""
    policy_version = selection['policy_version'] if selection else ROLLUP_POLICY if rollups else STATEMENT_POLICY if monthly else POLICY_VERSION
    monthly = policy_version in MONTHLY_STATEMENT_POLICIES
    if monthly:
        from .monthly_reports import select
        monthly_chosen = select(prime, year, as_of, selection)
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
        if monthly:
            report = monthly_chosen[month - 1]
            periods[-1]['monthly_revision'] = report['revision_id'] if report else None
            if report:
                periods[-1].update(actual_cutoff=report['cutoff'], stale=False)
    manifest = {'prime': prime, 'year': year, 'as_of': str(as_of), 'exact': exact,
                'policy_version': policy_version, 'revisions': [p['revision_id'] if p else None for p in chosen]}
    if monthly:
        manifest['monthly_revisions'] = [p['revision_id'] if p else None for p in monthly_chosen]
    lines = []
    for definition in taxonomy(prime, STATEMENT_POLICY if policy_version == ROLLUP_POLICY else policy_version):
        key = definition['id']
        cells = [p['document']['metrics'].get(key, metric(reason='monthly_summary_not_published')) if p else metric(reason='future_period' if periods[i]['state'] == 'future'
                  else 'source_not_published') for i, p in enumerate(chosen)]
        if monthly:
            for i, report in enumerate(monthly_chosen):
                if chosen[i]:
                    cells[i] = {**cells[i], 'source_ref': chosen[i]['revision_id']}
                if report and key in report['metrics']:
                    cells[i] = report['metrics'][key]
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
                if policy_version != FIRST_SLICE_POLICY:
                    kinds = {cells[i]['recognition'] for i in required}
                    if len(kinds) != 1:
                        total = metric(reason='mixed_recognition_bases')
                    else:
                        total['recognition'] = kinds.pop()
            else:
                total.update(status='partial', known_value=str(amount))
        lines.append({**definition, 'values': {p['key']: cells[i] for i, p in enumerate(periods)}, 'total': total})
    result = {'schema_version': '3.0', 'policy_version': policy_version, 'scope': 'individual', 'prime': prime,
              'year': year, 'as_of': str(as_of), 'periods': periods, 'lines': lines,
              'total_label': 'FY' if as_of >= date(year, 12, 31) else 'YTD',
              'source_manifest': {p['revision_id']: {'cutoff': p['cutoff'], **p['document']['source']} for p in chosen if p},
              'selection': selection_token(manifest), 'perimeter': 'operational_prime_management_reporting'}
    if monthly:
        result['source_manifest'].update({p['revision_id']: p['source'] for p in monthly_chosen if p})
    if policy_version == ROLLUP_POLICY:
        from .statement_rollups import extend
        result = extend(result)
    return {**result, 'report_hash': digest(result)}
