"""Spark fee classification from retained, reviewed financial source metrics.

The five SparkLend external streams are reserve-treasury spToken receipts.
They were previously included as allocation-linked rewards. Reclassify them,
without changing their amounts or treating vault performance fees as supply
yield. Morpho curator income needs an independently valued fee-share ledger.
The two additional markets come from the exact retained raw source revision.
"""
from .allocation_revenue import SPARK_EXTRA, allocation_label, native_projection
from .financial import metric, number
from .financial_allocations import ALLOCATIONS
from .spark_onchain import CURATORS
from .statement_rollups import combine

GAIN_LABELS = {'morpho': 'MORPHO', 'avax': 'AVAX', 'eth_weth': 'ETH / WETH',
               'sparklend_nonstable': 'SparkLend non-stable tokens, net of spETH depositors'}

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
            {'id': 'fees.curator', 'label': 'Morpho curator fees', 'aggregation': 'subtotal'},
            {'id': 'rewards.unattributed_sparklend_receipts', 'label': 'Unclassified SparkLend receipts',
             'family': 'Unclassified receipts', 'aggregation': 'leaf'},
            *({'id': key, 'label': symbol + ' reserve treasury receipts', 'aggregation': 'leaf'}
              for key, symbol in zip(UNMAPPED_RECEIPTS, ('spRLUSD', 'spUSDG'), strict=True)),
            *({'id': 'investment.sparklend.' + symbol, 'label': 'SparkLend ' + symbol.upper() + ' supply revenue',
               'family': 'SparkLend', 'aggregation': 'leaf'} for symbol in ('rlusd', 'usdg')),
            *({'id': 'fees.curator.' + venue, 'label': 'Morpho ' + venue + ' accrued fees',
               'aggregation': 'leaf'} for venue, *_ in CURATORS),
            *({'id': 'gains.' + key, 'label': label, 'aggregation': 'leaf'} for key, label in GAIN_LABELS.items()),
            *({'id': 'investment.venue.' + spec.venue, 'label': allocation_label(spec), 'family': spec.family,
               'aggregation': 'leaf'} for spec in (*ALLOCATIONS['spark'], *SPARK_EXTRA))] if prime == 'spark' else []


def saved_component(raw, field, publication, *, recognition='accrual', expected=None):
    if not raw or raw.get('status') not in ('available', 'partial'):
        return metric(reason='reporting_component_not_published')
    try:
        amount = number(raw[field])
    except (KeyError, ValueError):
        return metric(reason='invalid_reporting_component')
    cell = metric(amount, basis=raw.get('basis', 'pinned_onchain_reporting_component'),
                  source_paths=['reporting_spark_components.document'])
    if raw.get('confidence_reason'):
        cell['confidence_reason'] = raw['confidence_reason']
    end = raw.get('period_end', publication['cutoff'])
    if not publication['cutoff'][:7] + '-01' <= end <= publication['cutoff']:
        return metric(reason='invalid_reporting_component_bounds')
    cell.update(recognition=recognition, source_ref=publication['revision_id'], quality=raw.get('quality', 'supported'),
                period_start=publication['cutoff'][:7] + '-01', period_end=end)
    if raw['status'] == 'partial':
        cell.update(value=None, known_value=str(amount), status='partial', reason=raw.get('reason', 'incomplete_perimeter'))
    if expected and end < expected and cell['status'] == 'available':
        cell.update(value=None, known_value=str(amount), status='partial', reason='component_does_not_cover_selected_period')
    return cell


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


def extend(statement, chosen, supplemental):
    if statement['prime'] != 'spark':
        return
    lookup = {row['id']: row for row in statement['lines']}
    statement['spark_fee_ledger'] = []
    statement['spark_gain_ledger'] = []
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
    for period, publication, reference in zip(statement['periods'], chosen, supplemental, strict=True):
        month = period['key']
        valid = reserves_applied = False
        if period['state'] == 'future':
            fees['values'][month] = {**metric(reason='future_period'), 'status': 'future'}
        else:
            source = statement['source_manifest'].get(period['financial_revision'], {})
            original = {key: lookup[key]['values'][month] for key in RESERVE_RECEIPTS}
            reviewed = source.get('configuration_version') in REVIEWED_CONFIGURATIONS
            terms = [(key, 1, lookup[key]['values'][month] if reviewed
                      else metric(reason='reserve_treasury_attribution_not_reviewed'))
                     for key in RESERVE_RECEIPTS]
            extra = extra_receipts(publication)
            for key in UNMAPPED_RECEIPTS:
                lookup[key]['values'][month] = extra[key]
                terms.append((key, 1, lookup[key]['values'][month]))
                native_key = 'investment.sparklend.' + key.rsplit('.', 1)[-1]
                native = metric(reason='native_supply_source_unavailable')
                if extra[key]['status'] == 'available':
                    venue = 'S70' if key.endswith('rlusd') else 'S71'
                    row = next(r for r in publication['spark_fee_source_rows'] if r['venue_id'] == venue)
                    try:
                        native = metric(number(row['revenue']) - number(row['external_revenue']),
                                        basis='native_lending_yield', source_paths=[f'result.reporting_only_breakdown.{venue}.revenue_minus_external'])
                        native['source_ref'] = publication['revision_id']
                    except (KeyError, ValueError):
                        pass
                lookup[native_key]['values'][month] = native
            supplement = reference['document'] if reference else None
            valid = bool(supplement and supplement.get('schema_version') == 'spark-onchain.v1'
                         and supplement.get('cutoff') == reference['cutoff']
                         and supplement.get('period_start') == month + '-01')
            if valid:
                reserves = supplement.get('reserve_fees', {})
                try:
                    amounts = {key: number(reserves['amounts'][key.rsplit('.', 1)[-1]]) for key in components}
                    reserves_applied = reserves.get('status') in ('available', 'partial')
                except (KeyError, ValueError):
                    reserves_applied = False
                if reserves_applied:
                    for key in components:
                        if key in original and reviewed and original[key]['status'] == 'available' and publication['cutoff'] > reserves.get('period_end', reference['cutoff']):
                            continue
                        symbol = key.rsplit('.', 1)[-1]
                        cell = saved_component({**reserves, 'status': 'partial' if reserves.get('reason') == 'retained_after_refresh_failure' else 'available', 'value': reserves.get('amounts', {}).get(symbol)},
                                               'value', reference, recognition='receipt_in_kind', expected=period['expected_cutoff'])
                        cell['basis'] = 'onchain_reserve_treasury_spToken_receipts_at_stablecoin_par'
                        lookup[key]['values'][month] = cell
                    terms = [(key, 1, lookup[key]['values'][month]) for key in components]
                    if reserves.get('unknown_tokens'):
                        terms.append(('unclassified.other_sparklend_fee_tokens', 1, metric(reason='unvalued_reserve_receipt_tokens')))
                    reviewed = True
                fee_terms = []
                for venue, *_ in CURATORS:
                    raw = supplement.get('curator_fees', {}).get(venue, {})
                    cell = saved_component(raw, 'accrued_usd', reference, expected=period['expected_cutoff'])
                    key = 'fees.curator.' + venue
                    lookup[key]['values'][month] = cell
                    fee_terms.append((key, 1, cell))
                    statement['spark_fee_ledger'].append({**raw, 'month': month, 'venue': venue,
                        'source_ref': reference['revision_id'], 'period_start': month + '-01', 'period_end': raw.get('period_end', reference['cutoff'])})
                lookup['fees.curator']['values'][month] = {**combine(fee_terms),
                    'component_ids': [term[0] for term in fee_terms]}
                for key in GAIN_LABELS:
                    raw = supplement.get('gains', {}).get(key, {})
                    lookup['gains.' + key]['values'][month] = saved_component(raw, 'value', reference, expected=period['expected_cutoff'])
                    statement['spark_gain_ledger'].append({**raw, 'month': month, 'asset': key,
                        'source_ref': reference['revision_id'], 'period_start': month + '-01', 'period_end': raw.get('period_end', reference['cutoff'])})
            fees['values'][month] = {**combine(terms), 'component_ids': list(components)}
            if reserves_applied:
                if all(value['status'] == 'available' for value in original.values()) and publication['cutoff'] == reserves.get('period_end', reference['cutoff']):
                    difference = sum((number(value['value']) for value in original.values()), number('0')) - sum((amounts[key] for key in RESERVE_RECEIPTS), number('0'))
                    # A mismatch can be a settlement true-up spanning earlier
                    # months. It is not evidence of additional current income.
                    residual = metric('0' if difference == 0 else None,
                                      reason='retained_distributions_do_not_reconcile_to_receipt_month',
                                      basis='retained_distributions_less_tracked_treasury_receipts')
                    if difference == 0:
                        residual.update(recognition='receipt_in_kind', source_ref=publication['revision_id'],
                                        source_refs=[publication['revision_id'], reference['revision_id']])
                elif not any(value['status'] == 'available' for value in original.values()):
                    residual = saved_component({'status': 'available', 'value': '0', 'basis': 'no_retained_distributions_to_reconcile'},
                                               'value', reference, recognition='receipt_in_kind', expected=period['expected_cutoff'])
                else:
                    residual = metric(reason='retained_and_tracked_distributions_have_different_coverage')
            elif reviewed:
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
        if period['state'] != 'future' and not valid:
            lookup['fees.curator']['values'][month] = metric(reason='valued_morpho_fee_share_ledger_required')

    for row in statement['lines']:
        if row['id'].startswith('investment.'):
            row['label'] = row['label'].replace('net vault income', 'supply revenue').replace('supply yield', 'supply revenue')
    lookup['fees.curator']['components'] = [{'id': 'fees.curator.' + venue, 'sign': 1} for venue, *_ in CURATORS]
    native_projection(statement, chosen, supplemental, lookup, saved_component)
