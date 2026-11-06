"""Reviewed, dated position identities for management reporting policy v2.

Membership is explicit, not inferred from labels or pricing category. Both
Morpho USDT deployments are distinct positions in one business-income line.
No source config/provider reads occur while constructing a saved document.
"""
from dataclasses import dataclass
from decimal import Decimal, localcontext

EFFECTIVE_FROM = '2026-09-01'  # coverage reviewed against retained inputs


@dataclass(frozen=True)
class Allocation:
    venue: str
    key: str
    label: str
    category: str
    family: str


def a(venue, key, label, category, family):
    return Allocation(venue, key, label, category, family)


ALLOCATIONS = {
    'spark': (
        a('S1', 'sparklend.usds', 'SparkLend USDS supply yield', 'C', 'SparkLend'),
        a('S2', 'sparklend.usdc', 'SparkLend USDC supply yield', 'C', 'SparkLend'),
        a('S3', 'sparklend.usdt', 'SparkLend USDT supply yield', 'C', 'SparkLend'),
        a('S4', 'sparklend.dai', 'SparkLend DAI supply yield', 'C', 'SparkLend'),
        a('S5', 'sparklend.pyusd', 'SparkLend PYUSD supply yield', 'C', 'SparkLend'),
        a('S6', 'aave.eth_lido_usds', 'Aave Ethereum Lido USDS supply yield', 'C', 'Aave v3'),
        a('S7', 'aave.eth_usds', 'Aave Ethereum USDS supply yield', 'C', 'Aave v3'),
        a('S8', 'aave.eth_usdc', 'Aave Ethereum USDC supply yield', 'C', 'Aave v3'),
        a('S9', 'aave.eth_usdt', 'Aave Ethereum USDT supply yield', 'C', 'Aave v3'),
        a('S35', 'aave.base_usdc', 'Aave Base USDC supply yield', 'C', 'Aave v3'),
        a('S41', 'aave.arbitrum_usdc', 'Aave Arbitrum USDC supply yield', 'C', 'Aave v3'),
        a('S54', 'aave.avalanche_usdc', 'Aave Avalanche USDC supply yield', 'C', 'Aave v3'),
        a('S10', 'morpho.blue_chip_usdc', 'Morpho Blue Chip USDC net vault income', 'B', 'Morpho vaults'),
        a('S11', 'morpho.blue_chip_usdt', 'Morpho Blue Chip USDT net vault income', 'B', 'Morpho vaults'),
        a('S65', 'morpho.blue_chip_usdt', 'Morpho Blue Chip USDT net vault income', 'B', 'Morpho vaults'),
        a('S12', 'morpho.dai', 'Morpho DAI net vault income', 'B', 'Morpho vaults'),
        a('S13', 'morpho.usds', 'Morpho USDS net vault income', 'B', 'Morpho vaults'),
        a('S34', 'morpho.base_usdc', 'Morpho Base USDC net vault income', 'B', 'Morpho vaults'),
        a('S14', 'maple_syrup_usdc', 'Maple syrupUSDC net vault income', 'B', 'Maple'),
        a('S15', 'maple_syrup_usdt', 'Maple syrupUSDT net vault income', 'B', 'Maple'),
        a('S18', 'arkis.usdc', 'Arkis USDC net vault income', 'B', 'Arkis'),
        a('S16', 'ethena.susde', 'Ethena sUSDe net vault income', 'B', 'Other reviewed vaults'),
        a('S17', 'fluid.eth_usds', 'Fluid Ethereum USDS net vault income', 'B', 'Other reviewed vaults'),
        a('S36', 'fluid.base_usds', 'Fluid Base USDS net vault income', 'B', 'Other reviewed vaults'),
        a('S42', 'fluid.arbitrum_usds', 'Fluid Arbitrum USDS net vault income', 'B', 'Other reviewed vaults'),
    ),
    'grove': (
        a('E1', 'aave.horizon_rlusd', 'Aave Horizon RLUSD supply yield', 'C', 'Aave v3'),
        a('E2', 'aave.horizon_usdc', 'Aave Horizon USDC supply yield', 'C', 'Aave v3'),
        a('E3', 'aave.eth_rlusd', 'Aave Ethereum RLUSD supply yield', 'C', 'Aave v3'),
        a('E4', 'morpho.grove_usdc', 'Morpho Grove USDC net vault income', 'B', 'Morpho vaults'),
        a('E5', 'morpho.grove_usdc_high_yield', 'Morpho Grove USDC High Yield net vault income', 'B', 'Morpho vaults'),
        a('E6', 'morpho.grove_ausd', 'Morpho Grove AUSD net vault income', 'B', 'Morpho vaults'),
        a('E19', 'morpho.grove_base_usdc_high_yield', 'Morpho Grove Base USDC High Yield net vault income', 'B', 'Morpho vaults'),
        a('E23', 'morpho.grove_base_prime_usdc', 'Morpho Grove Base Prime USDC net vault income', 'B', 'Morpho vaults'),
        a('E24', 'morpho.grove_pyusd', 'Morpho Grove PYUSD net vault income', 'B', 'Morpho vaults'),
        a('E25', 'morpho.grove_monad_ausd', 'Morpho Grove Monad AUSD net vault income', 'B', 'Morpho vaults'),
        a('E37', 'maple_syrup_usdc', 'Maple syrupUSDC net vault income', 'B', 'Maple'),
    ),
    'osero': (a('O1', 'sparklend.usds', 'SparkLend USDS supply yield', 'C', 'SparkLend'),),
}


def taxonomy(prime):
    rows = {}
    for spec in ALLOCATIONS.get(prime, ()):
        rows['investment.' + spec.key] = {'id': 'investment.' + spec.key, 'label': spec.label,
            'family': spec.family, 'aggregation': 'leaf'}
        if spec.category == 'C':
            key = 'rewards.lending.' + spec.key
            rows[key] = {'id': key, 'label': spec.label.replace('supply yield', 'external rewards received'),
                         'family': 'Received lending rewards', 'aggregation': 'leaf'}
    if rows:
        for key, label in (('investment.reviewed_income', 'Reviewed native investment income subtotal'),
                           ('rewards.reviewed_lending', 'Reviewed external lending rewards received')):
            rows[key] = {'id': key, 'label': label, 'family': 'Reviewed components', 'aggregation': 'subtotal'}
    if prime == 'grove':
        rows['demand.chronicle_points'] = {'id': 'demand.chronicle_points',
            'label': 'Chronicle Points entitlement', 'family': 'Demand-side revenue', 'aggregation': 'leaf'}
    return list(rows.values())


def extend(document, source):
    from .financial import metric, number
    prime = source['prime']
    specs = ALLOCATIONS.get(prime, ())
    raw = source['result']
    rows = [(r, f'result.{name}.{r.get("venue_id")}') for name in ('venue_breakdown', 'reporting_only_breakdown')
            for r in raw.get(name, [])]
    if 'reporting_only_breakdown' not in raw:
        rows += [(r, f'result.display_only_breakdown.{r.get("venue_id")}') for r in raw.get('display_only_breakdown', [])]
    views = raw.get('venue_views', {}).get('reporting', [])
    effective = source['cutoff'] >= EFFECTIVE_FROM
    components, positions = {}, []

    def read(row, key):
        try:
            return number(row[key])
        except (KeyError, ValueError):
            return None

    def total(items, *, basis, recognition='accrual'):
        known = [number(m['value']) for m in items if m['status'] == 'available']
        with localcontext() as ctx:
            ctx.prec = 60
            amount = sum(known, Decimal(0))
        result = metric(amount if items and len(known) == len(items) else None,
                        reason='incomplete_reviewed_components', basis=basis,
                        source_paths=[p for m in items for p in m['source_paths']])
        if known and result['value'] is None:
            result.update(status='partial', known_value=str(amount))
        if result['value'] is not None:
            result['recognition'] = recognition
        return result

    for spec in specs:
        matches = [(r, path) for r, path in rows if r.get('venue_id') == spec.venue]
        view = [r for r in views if r.get('venue_id') == spec.venue]
        reason = ('policy_before_effective_date' if not effective else 'source_missing_or_duplicated'
                  if len(matches) != 1 else 'reporting_source_disabled_or_unavailable'
                  if view and (len(view) != 1 or not view[0].get('enabled') or view[0].get('status') != 'available')
                  else None)
        row, path = matches[0] if len(matches) == 1 else ({}, '')
        if not reason and (row.get('pricing_category') != spec.category or read(row, 'sd_share') != 0):
            reason = 'unsupported_source_category_or_ownership'
        income = metric(reason=reason or 'missing_source_input')
        rewards = metric(reason=reason or 'missing_source_input')
        if reason is None:
            revenue, external = read(row, 'revenue'), read(row, 'external_revenue')
            if revenue is not None and external is not None:
                with localcontext() as ctx:
                    ctx.prec = 60
                    income = metric(revenue - external, basis='native_lending_yield' if spec.category == 'C'
                                    else 'net_vault_yield_after_embedded_fees',
                                    source_paths=[path + '.revenue', path + '.external_revenue'])
            if spec.category == 'C':
                rewards = metric(external, reason='missing_source_input', basis='external_token_distributions_received',
                                 source_paths=[path + '.external_revenue'])
                if external is not None:
                    rewards['recognition'] = 'receipt'
        components.setdefault('investment.' + spec.key, []).append(income)
        if spec.category == 'C':
            components.setdefault('rewards.lending.' + spec.key, []).append(rewards)
        opening = metric(read(row, 'value_som') if reason is None else None,
                         reason=reason or 'missing_source_input', basis='reported_usd_position_valuation',
                         source_paths=[path + '.value_som'] if path else [])
        closing = metric(read(row, 'value_eom') if reason is None else None,
                         reason=reason or 'missing_source_input', basis='reported_usd_position_valuation',
                         source_paths=[path + '.value_eom'] if path else [])
        for value in (opening, closing):
            if value['value'] is not None:
                value['recognition'] = 'valuation'
        positions.append({'position_id': f'{prime}.{spec.venue}', 'source_venue_id': spec.venue,
                          'label': spec.label, 'opening_value': opening, 'closing_value': closing, 'income': income})
    for key, items in components.items():
        document['metrics'][key] = (items[0] if len(items) == 1 else total(items,
            basis='combined_disjoint_vault_deployments'))
    if specs:
        native = [m for k, ms in components.items() if k.startswith('investment.') for m in ms]
        received = [m for k, ms in components.items() if k.startswith('rewards.') for m in ms]
        document['metrics']['investment.reviewed_income'] = total(native, basis='reviewed_native_income_only')
        document['metrics']['rewards.reviewed_lending'] = total(received,
            basis='reviewed_external_token_distributions_received', recognition='receipt')
        included = {s.venue for s in specs}
        # A complete Osero perimeter includes its disjoint idle USDS balance.
        if prime == 'osero':
            matches = [(r, path) for r, path in rows if r.get('venue_id') == 'O2']
            cash, path = matches[0] if len(matches) == 1 else ({}, '')
            visible = not any(r.get('venue_id') == 'O2' and (not r.get('enabled') or r.get('status') != 'available') for r in views)
            valid = effective and len(matches) == 1 and visible and cash.get('pricing_category') == 'A' and read(cash, 'sd_share') == 0
            balances = [metric(read(cash, k) if valid else None, reason='idle_cash_source_unavailable',
                               basis='reported_usd_idle_cash', source_paths=[path + '.' + k] if path else [])
                        for k in ('value_som', 'value_eom')]
            for value in balances:
                if value['value'] is not None:
                    value['recognition'] = 'valuation'
            positions.append({'position_id': 'osero.O2', 'source_venue_id': 'O2', 'label': 'ALM idle USDS',
                              'opening_value': balances[0], 'closing_value': balances[1],
                              'income': metric(reason='idle_cash_has_no_certified_income_component')})
            included.add('O2')
        excluded = sorted({r.get('venue_id') for r, _ in rows} | {r.get('venue_id') for r in views} - included)
        excluded = [v for v in excluded if v and v not in included]
        portfolio = {'measure': 'tracked_allocations', 'perimeter_id': f'{prime}.reviewed-allocations.v2',
                     'positions': positions, 'expected_position_ids': [p['position_id'] for p in positions],
                     'excluded_source_venue_ids': excluded}
        for field in ('opening_value', 'closing_value'):
            result = total([p[field] for p in positions], basis='reviewed_disjoint_positions', recognition='valuation')
            if excluded:
                result.update(known_value=result['value'] if result['value'] is not None else result['known_value'],
                              value=None, status='partial', reason='unreviewed_positions')
            portfolio[field] = result
        document['portfolio'] = portfolio
    if prime == 'grove':
        document['metrics']['demand.chronicle_points'] = metric(
            read(raw, 'chronicle_points') if effective else None,
            reason='missing_source_input' if effective else 'policy_before_effective_date',
            basis='grove_chronicle_points_entitlement', source_paths=['result.chronicle_points'])
    return document
