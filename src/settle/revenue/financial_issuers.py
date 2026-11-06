"""Issuer compensation is a separate stream from native yield and asset value."""
from decimal import Decimal, localcontext

# Explicit source identity: venue, program, payer, recipient, receipt token.
ISSUERS = (
    ('S67', 'rlusd', 'RLUSD', '0xb70e0fad7d0cf4c187365a84e728802ab24ca78b',
     '0x1601843c5e9bc251a3272907010afa41fa18347e', '0x8292bb45bf1ee4d140127049757c2e0ff06317ed'),
    ('S68', 'pyusd', 'PYUSD', '0xfc0539d019482d311c161ae3b756cdccdec45e87',
     '0x1601843c5e9bc251a3272907010afa41fa18347e', '0x6c3ea9036406852006290770bedfcaba0e23a0e8'),
    ('S69', 'usdg', 'USDG', '0x5b01773138f17b38d1987558e576fa193db71a00',
     '0xfd2fd4b046136b540a56c11c75ac679ae7d1db24', '0x5fc5360d0400a0fd4f2af552add042d716f1d168'),
)


def taxonomy(prime):
    return [{'id': 'rewards.issuer.' + program, 'label': label + (' issuer receipts' if program == 'usdg' else ' issuer incentive accrual'),
             'family': 'Issuer compensation', 'aggregation': 'leaf'}
            for _, program, label, *_ in ISSUERS] if prime == 'spark' else []


def extend(document, source, *, legacy_paths=False):
    from .financial import metric, number
    if source['prime'] != 'spark':
        return document
    raw = source['result']
    reporting_collection = 'reporting_only_breakdown' if 'reporting_only_breakdown' in raw else 'display_only_breakdown'
    rows = [(r, name) for name in ('venue_breakdown', reporting_collection) for r in raw.get(name, [])]
    views = raw.get('venue_views', {}).get('reporting', [])
    claims = []
    for venue, program, label, payer, recipient, token in ISSUERS:
        income = metric(reason='reconciled_issuer_ledger_unavailable')
        receipts = metric(reason='reconciled_issuer_ledger_unavailable')
        receivable = metric(reason='reconciled_issuer_ledger_unavailable')
        prepaid = metric(reason='reconciled_issuer_ledger_unavailable')
        matches = [(r, name) for r, name in rows if r.get('venue_id') == venue]
        visible = not any(r.get('venue_id') == venue and (not r.get('enabled') or r.get('status') != 'available') for r in views)
        if len(matches) == 1 and visible and source['cutoff'] >= '2026-09-01':
            row, collection = matches[0]
            ledger = row.get('incentive_accrual', {})
            try:
                if not isinstance(ledger, dict):
                    raise ValueError('Missing issuer ledger')
                if row.get('pricing_category') != 'I' or row.get('settlement_enabled') is not False:
                    raise ValueError('Issuer claims must be separately tracked')
                if ledger.get('program') != program or ledger.get('cutoff') != source['cutoff'] or ledger.get('period_start') != raw['period']['start']:
                    raise ValueError('Issuer ledger does not cover this reporting period')
                sources = ledger.get('receipt_sources', [])
                if not isinstance(sources, list) or len(sources) != 1 or not isinstance(sources[0], dict) or any(str(sources[0].get(k, '')).lower() != v for k, v in
                                           (('payer', payer), ('recipient', recipient), ('token', token))):
                    raise ValueError('Unreviewed issuer receipt attribution')
                if sources[0].get('chain', 'ethereum') != ('robinhood' if program == 'usdg' else 'ethereum'):
                    raise ValueError('Unreviewed receipt chain')
                opening, accrued, paid, closing = (number(ledger[k]) for k in
                    ('period_opening_balance', 'period_accrual', 'period_receipts', 'accrued_balance'))
                with localcontext() as ctx:
                    ctx.prec = 60
                    if abs(opening + accrued - paid - closing) > Decimal('1e-12') or paid < 0:
                        raise ValueError('Unreconciled issuer balance')
                if (number(row['revenue']) != accrued or number(row['value_som']) != opening
                        or number(row['value_eom']) != closing or number(row['external_revenue']) != 0
                        or number(row['sd_share']) != 0):
                    raise ValueError('Issuer ledger and saved venue disagree')
                if program == 'usdg' and (accrued != paid or closing != 0 or opening != 0):
                    raise ValueError('USDG uses verified receipts as its proxy')
                # Retained v2 documents keep their original legacy path spelling.
                origin = 'venue_breakdown' if legacy_paths and collection == 'display_only_breakdown' else collection
                path = f'result.{origin}.{venue}.incentive_accrual'
                income = metric(accrued, basis='issuer_receipt_proxy' if program == 'usdg' else 'issuer_compensation_daily_accrual',
                                source_paths=[path + '.period_accrual'])
                if program == 'usdg':
                    income['recognition'] = 'receipt'
                receipts = metric(paid, basis='attributed_issuer_payments', source_paths=[path + '.period_receipts'])
                receipts['recognition'] = 'receipt'
                receivable = metric(max(closing, Decimal(0)), basis='issuer_compensation_receivable',
                                    source_paths=[path + '.accrued_balance'])
                prepaid = metric(max(-closing, Decimal(0)), basis='issuer_compensation_received_in_advance',
                                 source_paths=[path + '.accrued_balance'])
                receivable['recognition'] = prepaid['recognition'] = 'valuation'
            except (KeyError, ValueError, TypeError):
                income = metric(reason='invalid_or_incomplete_issuer_ledger')
        document['metrics']['rewards.issuer.' + program] = income
        claims.append({'source_venue_id': venue, 'label': label + ' issuer compensation',
                       'income': income, 'cash_received': receipts, 'receivable': receivable,
                       'received_in_advance': prepaid, 'included_in_allocation_value': False})
    document['claims'] = claims
    return document
