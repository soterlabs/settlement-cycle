"""Reviewed Grove STAC/JAAA income from saved sources; no provider calls.

Fund returns include NAV movements and distributions net of capital flows and
the saved Sky allocation. They are investment returns, not contractual interest.
"""
import re
from decimal import Decimal, localcontext

from .financial import metric, number

SPECS = (
    ('E7', 'investment.rwa.stac', 'STAC', 'Securitize Tokenized AAA CLO Fund (STAC)'),
    ('E8', 'investment.rwa.jaaa', 'JAAA', 'Janus Henderson Anemoy AAA CLO (JAAA)'),
)


def taxonomy(prime):
    return [{'id': key, 'label': f'{symbol} — prime-retained investment income',
             'family': 'RWA funds', 'aggregation': 'leaf'}
            for _, key, symbol, _ in SPECS] if prime == 'grove' else []


def _income(gross, retained, sky, share, *, source_ref, path, tolerance):
    with localcontext() as ctx:
        ctx.prec = 60
        delta = gross - retained - sky
    if not 0 <= share <= 1 or abs(delta) > tolerance:
        raise ValueError('RWA ownership split does not reconcile')
    result = metric(retained, basis='prime_retained_rwa_investment_return_after_sde', source_paths=[path])
    result.update(recognition='investment_return', source_ref=source_ref,
                  allocation_split={'gross_income_usd': str(gross), 'sky_income_usd': str(sky),
                    'prime_income_usd': str(retained), 'sky_share': str(share), 'rounding_delta_usd': str(delta)})
    return result


def daily(publication):
    """Read the exact raw revision underlying the selected financial document."""
    result = {key: metric(reason='rwa_source_not_published') for _, key, *_ in SPECS}
    if not publication or publication['cutoff'] < '2026-01-01':
        return result
    document = publication['document']
    if (document['prime'] != 'grove'
            or publication.get('rwa_source_hash') != document['source']['result_hash']):
        return result
    rows = publication.get('rwa_source_rows', [])
    for venue, key, _, label in SPECS:
        matches = [row for row in rows if row.get('venue_id') == venue]
        try:
            if len(matches) != 1:
                raise ValueError('Missing or duplicate RWA source')
            views = [view for view in publication.get('rwa_source_views', []) if view.get('venue_id') == venue]
            if views and (len(views) != 1 or not views[0].get('enabled') or views[0].get('status') != 'available'):
                raise ValueError('Disabled or unavailable RWA source')
            row = matches[0]
            if (row.get('pricing_category') != 'E' or row.get('label') != label
                    or number(row['external_revenue']) != 0):
                raise ValueError('Unsupported RWA identity or additional rewards')
            result[key] = _income(*(number(row[field]) for field in
                ('actual_revenue', 'revenue', 'sd_revenue', 'sd_share')),
                source_ref=publication['revision_id'], path=f'result.venue_breakdown.{venue}.revenue',
                tolerance=Decimal('1e-12'))
        except (KeyError, TypeError, ValueError):
            result[key] = metric(reason='invalid_or_incomplete_rwa_source')
    return result


def _amount(raw):
    if not re.fullmatch(r'-?\$(?:\d+|\d{1,3}(?:,\d{3})+)\.\d{2}', raw):
        raise ValueError('Invalid published RWA currency amount')
    return number(raw.replace('$', '').replace(',', ''))


def monthly(publication):
    """Header-based parsing supports the historical per-venue table layouts."""
    result = {key: metric(reason='rwa_source_not_published') for _, key, *_ in SPECS}
    if not publication or publication['prime'] != 'grove' or publication['cutoff'] < '2026-01-01':
        return result
    text = publication['raw_summary']
    if text.count('\n## Per-venue\n') != 1:
        raise ValueError('Missing or ambiguous published RWA table')
    section = text.split('\n## Per-venue\n')[1].split('\n## ')[0]
    cells = [[cell.strip() for cell in row.split('|')[1:-1]] for row in section.splitlines() if row.startswith('|')]
    required = {'Venue', 'Label', 'actual_rev', 'revenue', 'sd_revenue', 'sd_share'}
    if (len(cells) < 3 or not required <= set(cells[0]) or len(set(cells[0])) != len(cells[0])
            or len(cells[1]) != len(cells[0]) or any(not re.fullmatch(r':?-{3,}:?', cell) for cell in cells[1])
            or any(len(row) != len(cells[0]) for row in cells[2:])):
        raise ValueError('Invalid published RWA table schema')
    rows = [dict(zip(cells[0], row, strict=True)) for row in cells[2:]]
    for venue, key, _, label in SPECS:
        matches = [row for row in rows if row['Venue'] == venue]
        if len(matches) != 1 or matches[0]['Label'] != label:
            raise ValueError('Missing, duplicate or changed published RWA identity')
        row = matches[0]
        if not re.fullmatch(r'\d+(?:\.\d+)?%', row['sd_share']):
            raise ValueError('Invalid published RWA share')
        # The published share is rounded: use the reported retained amount,
        # rather than allocating gross income a second time with that share.
        result[key] = _income(*(_amount(row[field]) for field in ('actual_rev', 'revenue', 'sd_revenue')),
            number(row['sd_share'][:-1]) / 100, source_ref=publication['revision_id'],
            path=publication['source']['url'] + '#per-venue', tolerance=Decimal('0.015'))
    return result


def refresh_reviewed_subtotal(statement):
    from .statement_rollups import combine
    if statement['prime'] != 'grove':
        return
    native = [row for row in statement['lines'] if row['id'].startswith('investment.')
              and row.get('aggregation') == 'leaf']
    subtotal = next(row for row in statement['lines'] if row['id'] == 'investment.reviewed_income')
    subtotal['label'] = 'Reviewed investment income subtotal'
    subtotal['components'] = [{'id': row['id'], 'sign': 1} for row in native]
    for period in statement['periods']:
        if period['state'] != 'future':
            key = period['key']
            subtotal['values'][key] = combine([(row['id'], 1, row['values'][key]) for row in native])
            # Existing leaf validators expect a single source; this subtotal
            # may combine a published monthly RWA amount and a daily document.
            subtotal['values'][key]['component_ids'] = [row['id'] for row in native]
