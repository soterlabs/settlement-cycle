"""Spark fee classification from retained, reviewed financial source metrics.

The five SparkLend external streams are reserve-treasury spToken receipts.
They were previously included as allocation-linked rewards. Reclassify them,
without changing their amounts or treating vault performance fees as supply
yield. Morpho curator income needs an independently valued fee-share ledger.
The two additional markets come from the exact retained raw source revision.
"""
from .financial import metric, number
from .statement_rollups import combine

RESERVE_RECEIPTS = tuple('rewards.lending.sparklend.' + symbol
                         for symbol in ('usds', 'usdc', 'usdt', 'dai', 'pyusd'))
UNMAPPED_RECEIPTS = ('fees.sparklend.rlusd', 'fees.sparklend.usdg')
# These retained configurations route external SparkLend spToken income only
# from the two reviewed reserve treasuries. Unknown configurations require
# renewed attribution review; an aggregate external reward is not proof.
REVIEWED_CONFIGURATIONS = {
    '244e91c22e06b4cbf1a573239571363f057c4c8ce9d40dc2e2735fd07f594f43',
    '1396a71440f5476ce8a9b9d87eb3bdfc587861cd139ad0481d76d4ed26314529',
    'da77310707751cae619941f4a35e937d8f880167b5af57a58ae5db20376c7067',
}


def taxonomy(prime):
    return [{'id': 'fees.sparklend', 'label': 'SparkLend fees', 'aggregation': 'subtotal'},
            {'id': 'fees.curator', 'label': 'Morpho curator fees', 'aggregation': 'leaf'},
            {'id': 'rewards.unattributed_sparklend_receipts', 'label': 'Unclassified SparkLend receipts',
             'family': 'Unclassified receipts', 'aggregation': 'leaf'},
            *({'id': key, 'label': symbol + ' reserve treasury receipts', 'aggregation': 'leaf'}
              for key, symbol in zip(UNMAPPED_RECEIPTS, ('spRLUSD', 'spUSDG'), strict=True))] if prime == 'spark' else []


def extra_receipts(publication):
    result = {key: metric(reason='reserve_token_not_in_retained_financial_perimeter') for key in UNMAPPED_RECEIPTS}
    if (not publication or publication['cutoff'] < '2026-09-01'
            or publication['document']['prime'] != 'spark'
            or publication['document']['cutoff'] != publication['cutoff']
            or publication.get('spark_fee_source_hash') != publication['document']['source']['result_hash']):
        return result
    if publication['document']['source']['configuration_version'] not in REVIEWED_CONFIGURATIONS:
        return result
    for venue, key in zip(('S70', 'S71'), UNMAPPED_RECEIPTS, strict=True):
        rows = [r for r in publication.get('spark_fee_source_rows', []) if r.get('venue_id') == venue]
        views = [r for r in publication.get('spark_fee_source_views', []) if r.get('venue_id') == venue]
        if (len(rows) != 1 or rows[0].get('pricing_category') != 'C'
                or len(views) != 1 or not views[0].get('enabled') or views[0].get('status') != 'available'):
            continue
        try:
            if number(rows[0]['sd_share']) != 0:
                continue
            value = number(rows[0]['external_revenue'])
        except (KeyError, ValueError):
            continue
        result[key] = metric(value, basis='sparklend_reserve_treasury_spToken_receipts',
                             source_paths=[f'result.reporting_only_breakdown.{venue}.external_revenue'])
        result[key].update(recognition='receipt_in_kind', source_ref=publication['revision_id'])
    return result


def extend(statement, chosen):
    if statement['prime'] != 'spark':
        return
    lookup = {row['id']: row for row in statement['lines']}
    for key in RESERVE_RECEIPTS:
        row = lookup[key]
        row['label'] = row['label'].replace('external rewards received', 'reserve treasury receipts')
        row['family'] = 'SparkLend fees'
        for period in statement['periods']:
            cell = row['values'][period['key']]
            source = statement['source_manifest'].get(period['financial_revision'], {})
            if cell['status'] == 'available' and source.get('configuration_version') in REVIEWED_CONFIGURATIONS:
                cell.update(recognition='receipt_in_kind',
                            basis='sparklend_reserve_treasury_spToken_receipts')
    fees = lookup['fees.sparklend']
    components = (*RESERVE_RECEIPTS, *UNMAPPED_RECEIPTS)
    fees['components'] = [{'id': key, 'sign': 1} for key in components]
    for period, publication in zip(statement['periods'], chosen, strict=True):
        month = period['key']
        if period['state'] == 'future':
            fees['values'][month] = {**metric(reason='future_period'), 'status': 'future'}
        else:
            source = statement['source_manifest'].get(period['financial_revision'], {})
            reviewed = source.get('configuration_version') in REVIEWED_CONFIGURATIONS
            terms = [(key, 1, lookup[key]['values'][month] if reviewed
                      else metric(reason='reserve_treasury_attribution_not_reviewed'))
                     for key in RESERVE_RECEIPTS]
            extra = extra_receipts(publication)
            for key in UNMAPPED_RECEIPTS:
                lookup[key]['values'][month] = extra[key]
                terms.append((key, 1, lookup[key]['values'][month]))
            fees['values'][month] = {**combine(terms), 'component_ids': list(components)}
            if reviewed:
                residual = metric('0', basis='all_sparklend_receipts_attributed_to_reserve_fees')
                residual.update(recognition='receipt_in_kind', source_ref=period['financial_revision'])
            else:
                # Losing an attribution review must not erase known income.
                # Preserve it outside fees until the source is reviewed again.
                residual = combine([(key, 1, lookup[key]['values'][month]) for key in RESERVE_RECEIPTS])
                residual.update(basis='received_sparklend_distributions_pending_fee_attribution',
                                quality='estimate')
                if period['financial_revision']:
                    residual['source_ref'] = period['financial_revision']
            lookup['rewards.unattributed_sparklend_receipts']['values'][month] = residual
        # The fee recipient is Spark, but neither the configured percentage
        # nor the complete vault return proves how much fee income was earned.
        if period['state'] != 'future':
            lookup['fees.curator']['values'][month] = metric(reason='valued_morpho_fee_share_ledger_required')
