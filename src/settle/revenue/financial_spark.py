"""Spark fee classification from retained, reviewed financial source metrics.

The five SparkLend external streams are reserve-treasury spToken receipts.
They were previously included as allocation-linked rewards. Reclassify them,
without changing their amounts or treating vault performance fees as supply
yield. Morpho curator income needs an independently valued fee-share ledger.
"""
from .financial import metric
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
}


def taxonomy(prime):
    return [{'id': 'fees.sparklend', 'label': 'SparkLend fees', 'aggregation': 'subtotal'},
            {'id': 'fees.curator', 'label': 'Morpho curator fees', 'aggregation': 'leaf'},
            {'id': 'rewards.unattributed_sparklend_receipts', 'label': 'Unclassified SparkLend receipts',
             'family': 'Unclassified receipts', 'aggregation': 'leaf'},
            *({'id': key, 'label': symbol + ' reserve treasury receipts', 'aggregation': 'leaf'}
              for key, symbol in zip(UNMAPPED_RECEIPTS, ('spRLUSD', 'spUSDG'), strict=True))] if prime == 'spark' else []


def extend(statement):
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
    for period in statement['periods']:
        month = period['key']
        if period['state'] == 'future':
            fees['values'][month] = {**metric(reason='future_period'), 'status': 'future'}
        else:
            source = statement['source_manifest'].get(period['financial_revision'], {})
            reviewed = source.get('configuration_version') in REVIEWED_CONFIGURATIONS
            terms = [(key, 1, lookup[key]['values'][month] if reviewed
                      else metric(reason='reserve_treasury_attribution_not_reviewed'))
                     for key in RESERVE_RECEIPTS]
            for key in UNMAPPED_RECEIPTS:
                lookup[key]['values'][month] = metric(reason='reserve_token_not_in_retained_financial_perimeter')
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
