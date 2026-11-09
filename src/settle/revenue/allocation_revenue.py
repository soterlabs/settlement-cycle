"""Monthly native supply revenue per venue, before global borrowing costs."""
from .financial import metric, number
from .financial_allocations import ALLOCATIONS, a
from .statement_rollups import combine

SPARK_EXTRA = (
    a('S70', 'sparklend.rlusd', 'SparkLend RLUSD supply revenue', 'C', 'SparkLend'),
    a('S71', 'sparklend.usdg', 'SparkLend USDG supply revenue', 'C', 'SparkLend'),
)


def allocation_label(spec):
    label = spec.label.removesuffix(' net vault income').removesuffix(' supply yield').removesuffix(' supply revenue')
    prefix = {'Maple': 'Maple ', 'SparkLend': 'SparkLend ', 'Morpho vaults': 'Morpho ', 'Aave v3': 'Aave ', 'Arkis': 'Arkis '}.get(spec.family)
    if prefix:
        label = label.removeprefix(prefix)
    if spec.venue == 'S11':
        label += ' (old vault)'
    return label


def native_projection(statement, chosen, supplemental, lookup, component_metric):
    from decimal import Decimal, localcontext
    statement['spark_allocation_ledger'] = []
    specs = (*ALLOCATIONS['spark'], *SPARK_EXTRA)
    groups = {}
    for spec in specs:
        groups.setdefault('investment.' + spec.key, []).append('investment.venue.' + spec.venue)
    for key, ids in groups.items():
        lookup[key].update(aggregation='subtotal', components=[{'id': item, 'sign': 1} for item in ids])
    for period, publication, supplement in zip(statement['periods'], chosen, supplemental, strict=True):
        if period['state'] == 'future':
            continue
        month = period['key']
        positions = {row['source_venue_id']: row for row in publication['document']['portfolio']['positions']} if publication else {}
        raw_rows = publication.get('spark_allocation_rows', []) if publication else []
        supplied = supplement['document'].get('native_supplies', {}) if supplement else {}
        for spec in specs:
            cell = positions.get(spec.venue, {}).get('income', metric(reason='native_supply_source_unavailable'))
            if spec.venue in ('S70', 'S71'):
                cell = lookup['investment.' + spec.key]['values'][month]
            average = None
            rows = [row for row in raw_rows if row.get('venue_id') == spec.venue]
            if len(rows) == 1 and isinstance(rows[0].get('tw_avg_value'), str):
                average = number(rows[0]['tw_avg_value'])
            reference = publication
            raw = supplied.get(spec.venue, {})
            if supplement and raw.get('status') in ('available', 'partial') and (
                    cell['status'] != 'available' or not publication or supplement['cutoff'] > publication['cutoff']
                    or (raw.get('reconciliation') and supplement['cutoff'] >= publication['cutoff'])
                    or (spec.venue in ('S36', 'S42') and supplement['cutoff'] >= publication['cutoff'])):
                cell = component_metric(raw, 'value', supplement, expected=period['expected_cutoff'])
                average = number(raw['average_position_usd']) if isinstance(raw.get('average_position_usd'), str) else average if publication and publication['cutoff'] == supplement['cutoff'] else None
                reference = supplement
            elif publication and cell['status'] == 'available':
                cell = {**cell, 'source_ref': publication['revision_id'], 'period_start': month + '-01', 'period_end': publication['cutoff']}
                if publication['cutoff'] < period['expected_cutoff']:
                    cell.update(status='partial', known_value=cell['value'], value=None, reason='native_source_does_not_cover_selected_period')
            lookup['investment.venue.' + spec.venue]['values'][month] = cell
            amount = cell['value'] if cell['status'] == 'available' else cell.get('known_value')
            apr = apy = None
            if average is not None and average > 0 and amount is not None and reference:
                from datetime import date
                days = (date.fromisoformat(cell.get('period_end', reference['cutoff'])) - date.fromisoformat(month + '-01')).days + 1
                with localcontext() as ctx:
                    ctx.prec = 60
                    rate = number(amount) / average
                    apr = rate * Decimal(365) / days
                    if 1 + rate > 0:
                        apy = (1 + rate) ** (Decimal(365) / days) - 1
            statement['spark_allocation_ledger'].append({'month': month, 'venue_id': spec.venue, 'family': spec.family,
                'label': allocation_label(spec),
                'native_revenue_usd': cell, 'average_position_usd': str(average) if average is not None else None,
                'supply_apr': str(apr) if apr is not None else None, 'supply_apy': str(apy) if apy is not None else None})
        for key, ids in groups.items():
            lookup[key]['values'][month] = {**combine([(item, 1, lookup[item]['values'][month]) for item in ids]),
                                           'component_ids': ids}


def month_projection(statement, month, venue=None):
    rows = [row for row in statement.get('spark_allocation_ledger', []) if row['month'] == month and (venue is None or row['venue_id'] == venue)]
    families = sorted({row['family'] for row in rows})
    return {'prime': statement['prime'], 'month': month, 'selection': statement['selection'],
            'report_hash': statement['report_hash'], 'source_manifest': statement['source_manifest'],
            'period': next(p for p in statement['periods'] if p['key'] == month),
            'count': len(rows), 'allocations': rows,
            'family_totals': {family: combine([(row['venue_id'], 1, row.get('allocation_revenue_usd', row['native_revenue_usd'])) for row in rows if row['family'] == family]) for family in families},
            'funding_cost_deducted': False, 'external_rewards_included': False, 'embedded_vault_fees_deducted': True,
            **({'self_curation_rebates_separately_disclosed': True} if 'review_policy' in statement else {}),
            'rate_basis': 'observed supply return on average position; ratio units, 0.05 = 5%; not a forward advertised rate'}
