"""Explicit signed management rollups; unknown coverage never implies zero.

Only leaf metrics enter the reviewed investment/reward buckets. Family and
reviewed subtotals are alternative views and never added to their own leaves.
"""
from decimal import Decimal, localcontext

from .financial import metric, number


def definitions(prime, source_definitions, *, allocation_rewards=False):
    native = [item['id'] for item in source_definitions if item['id'].startswith('investment.')
              and item.get('aggregation') != 'subtotal'
              and (item.get('aggregation') == 'leaf' or prime == 'obex')]
    rewards = [item['id'] for item in source_definitions if item['id'].startswith('rewards.')
               and item.get('aggregation') == 'leaf']
    # Only reviewed, explicitly allocation-linked streams move into allocation
    # income. New/unclassified reward types stay outside this perimeter.
    linked = [key for key in rewards if allocation_rewards and
              key.startswith(('rewards.lending.', 'rewards.issuer.'))]
    other_rewards = [key for key in rewards if key not in linked]
    rows = []

    def add(key, label, ids):
        rows.append({'id': 'statement.' + key, 'label': label, 'aggregation': 'subtotal',
                     'components': [{'id': item[0], 'sign': item[1]} if isinstance(item, tuple)
                                    else {'id': item, 'sign': 1} for item in ids]})

    demand = ['demand.agent_rate', 'demand.distribution_rewards']
    if prime == 'skybase':
        demand.append('demand.gar')
    if prime == 'grove':
        demand.append('demand.chronicle_points')
    add('demand', 'Demand-side revenue', demand)
    rows[-1]['published_override'] = 'demand.total'
    families = {}
    for item in source_definitions:
        if item['id'] in native:
            families.setdefault(item.get('family', 'Maple'), []).append(item['id'])
    for family, ids in families.items():
        slug = family.lower().replace(' ', '_')
        add('family.' + slug, 'Reviewed ' + family + ' income', ids)
        rows[-1]['family'] = family
    if linked:
        add('family.allocation_rewards', 'Allocation-linked incentives and rewards', linked)
    add('interest', 'Income from allocations' if allocation_rewards else 'Interest income from allocations',
        [*native, *linked, 'unclassified.allocation_income'])
    add('rewards', 'Other incentives and rewards received' if allocation_rewards else 'Incentives and rewards received',
        [*other_rewards, 'unclassified.other_rewards'])
    add('noninterest_income', 'Noninterest income', ['statement.demand', 'statement.rewards',
        'unclassified.protocol_fees', 'unclassified.curator_fees', 'unclassified.nonstable_gains'])
    add('funding', 'Interest expense (cost of funding)', ['funding.sky.net', 'unclassified.other_funding'])
    add('net_interest', 'Net interest income', ['statement.interest', ('statement.funding', -1)])
    add('net_revenue', 'Total net revenue', ['statement.noninterest_income', 'statement.net_interest'])
    add('noninterest_expense', 'Total noninterest expense', ['unclassified.operating_expenses',
        'unclassified.other_expenses', 'unclassified.incentives_expense',
        'unclassified.distribution_costs', 'unclassified.credit_impairment'])
    add('net_income', 'Net income', ['statement.net_revenue', ('statement.noninterest_expense', -1),
        ('unclassified.income_tax', -1)])
    return rows


def combine(terms):
    """terms are (id, sign, metric); include each known portion exactly once."""
    amount, count, missing, sources, kinds = Decimal(0), 0, [], set(), set()
    with localcontext() as ctx:
        ctx.prec = 60
        for key, sign, item in terms:
            known = item['value'] if item['status'] == 'available' else item.get('known_value')
            if known is not None:
                amount += sign * number(known)
                count += 1
                kinds.add(item['recognition'])
            if item['status'] != 'available':
                missing.extend(item.get('missing_components') or [key])
            sources.update(item.get('source_refs', []))
            if item.get('source_ref'):
                sources.add(item['source_ref'])
    result = metric(amount if count and not missing else None, reason='missing_components',
                    basis='explicit_signed_management_components')
    if count and missing:
        result.update(status='partial', known_value=str(amount))
    result.update(missing_components=sorted(set(missing)), source_refs=sorted(sources),
                  recognition=next(iter(kinds)) if len(kinds) == 1 else 'mixed_management_components' if kinds else 'unknown')
    return result


def extend(statement):
    source_definitions = statement['lines'][:]
    from .financial import ALLOCATION_REWARDS_POLICY, RWA_POLICIES
    rows = definitions(statement['prime'], source_definitions,
                       allocation_rewards=statement['policy_version'] == ALLOCATION_REWARDS_POLICY)
    if statement['policy_version'] in RWA_POLICIES and statement['prime'] == 'grove':
        for row in rows:
            if row['id'] == 'statement.interest':
                row['label'] = 'Investment income from allocations'
            elif row['id'] == 'statement.net_interest':
                row['label'] = 'Net investment income after funding'
    lookup = {row['id']: row for row in source_definitions}
    for row in rows:
        cells = {}
        for period in statement['periods']:
            month = period['key']
            if period['state'] == 'future':
                cell = metric(reason='future_period')
                cell['status'] = 'future'
            else:
                override = lookup.get(row.get('published_override', ''), {}).get('values', {}).get(month)
                if override and override['status'] == 'available':
                    # Preserve the published total and its cent-rounding bridge.
                    cell = {**override, 'missing_components': [],
                            'component_ids': [row['published_override']],
                            'source_refs': [override['source_ref']]}
                else:
                    cell = combine([(term['id'], term['sign'],
                        lookup.get(term['id'], {}).get('values', {}).get(month, metric(reason='unclassified_component')))
                        for term in row['components']])
                    cell['component_ids'] = [term['id'] for term in row['components']]
            cells[month] = cell
        total = combine([(period['key'], 1, cells[period['key']])
                         for period in statement['periods'] if period['state'] != 'future'])
        published = {**row, 'values': cells, 'total': total}
        statement['lines'].append(published)
        lookup[row['id']] = published
    # Existing leaf/reviewed totals must also retain partially known months.
    for row in source_definitions:
        row['total'] = combine([(period['key'], 1, row['values'][period['key']])
                                for period in statement['periods'] if period['state'] != 'future'])
    return statement
