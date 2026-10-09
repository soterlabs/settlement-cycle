#!/usr/bin/env python3
"""Separate witnessed execution costs from remaining transaction discrepancies.

A partial match retains its signed difference. Nothing is reclassified in the
capital ledger, and historical transaction values are not monthly borrowing costs.
"""
import argparse
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path

from audit_spark_ethena_execution import audit as audit_ethena
from audit_spark_par_swaps import audited_rules
from audit_spark_susds_swaps import audit as audit_susds

ZERO = D(0)


def decompose(outflows, witnesses, recognized_income):
    canonical = {k: ':'.join(k.split(':')[:2]) for k in outflows}
    counts = Counter(canonical.values())
    rows = []
    for identity, raw in outflows.items():
        observed = D(raw)
        if not observed.is_finite() or observed < 0:
            raise ValueError('Invalid observed transaction residual')
        key = canonical[identity]
        # A witness describes the whole transaction. Do not apply it twice to
        # separately modeled routes within that transaction.
        components = witnesses.get(key, {}) if counts[key] == 1 else {}
        income = D(recognized_income.get(key, 0)) if components else ZERO
        expected = -sum((D(v) for v in components.values()), ZERO) + income
        if not expected.is_finite() or not income.is_finite():
            raise ValueError('Invalid execution witness')
        remainder = observed - expected
        rows.append({'identity': identity, 'observed_outflow': str(observed),
                     'witnessed_components_signed_gain': {k: str(v) for k, v in components.items()},
                     'already_recognized_income': str(income),
                     'witnessed_net_outflow': str(expected),
                     'remaining_signed_difference': str(remainder),
                     'ambiguous_split_transaction': counts[key] > 1})
    return {'scope': __doc__, 'transactions': len(rows),
            'observed_outflows': str(sum((D(r['observed_outflow']) for r in rows), ZERO)),
            'witnessed_net_outflows': str(sum((D(r['witnessed_net_outflow']) for r in rows), ZERO)),
            'remaining_signed_difference': str(sum((D(r['remaining_signed_difference']) for r in rows), ZERO)),
            'remaining_absolute_difference': str(sum((abs(D(r['remaining_signed_difference'])) for r in rows), ZERO)),
            'rows': rows}


def read(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == '.gz' else raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('residuals', 'par-events', 'susds-events', 'ethena-events', 'income-rules', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--legacy-curve-events', type=Path,
                        help='Optional independent old USDC/USDT and PYUSD/USDC swap evidence')
    args = parser.parse_args()
    inputs = [args.residuals, args.par_events, args.susds_events, args.ethena_events, args.income_rules]
    if args.legacy_curve_events:
        inputs.append(args.legacy_curve_events)
    if args.output.resolve() in {p.resolve() for p in inputs}:
        raise ValueError('Audit output cannot overwrite evidence')
    par, _, rules = audited_rules(read(args.par_events))
    registry = read(args.income_rules)
    if (registry['rows'] != rules
            or registry['evidence_sha256'] != hashlib.sha256(args.par_events.read_bytes()).hexdigest()):
        raise ValueError('Recognized income rules differ from witnessed par swaps')
    susds = audit_susds(read(args.susds_events))['rows']
    ethena = audit_ethena(read(args.ethena_events))
    witnesses = defaultdict(dict)
    for kind, records, field, sign in (('par-swap', par, 'gain', 1),
                                       ('susds-curve', susds, 'gain', 1),
                                       ('ethena', ethena, 'par_value_shortfall', -1)):
        for row in records:
            if kind in witnesses[row['identity']]:
                raise ValueError('Duplicate transaction execution witness')
            witnesses[row['identity']][kind] = sign * D(row[field])
    legacy_summary = None
    if args.legacy_curve_events:
        from audit_spark_legacy_curve_swaps import audit as audit_legacy

        legacy, excluded = audit_legacy(read(args.legacy_curve_events))
        for row in legacy:
            if 'legacy-curve' in witnesses[row['identity']]:
                raise ValueError('Duplicate legacy Curve transaction')
            witnesses[row['identity']]['legacy-curve'] = D(row['gain'])
        legacy_summary = {'verified': len(legacy), 'excluded': excluded,
                          'signed_gain': str(sum((D(r['gain']) for r in legacy), ZERO))}
    residuals = read(args.residuals)
    if 'unmatched_outflows' in residuals:
        outflows = residuals['unmatched_outflows']
    else:
        outflows = {r['identity']: r['amount'] for r in residuals['outflows']}
        if len(outflows) != len(residuals['outflows']):
            raise ValueError('Duplicate observed residual identity')
    result = decompose(outflows, witnesses, {r['identity']: r['earned'] for r in rules})
    dependencies = [Path(__file__), *(Path(__file__).with_name(name) for name in (
        'audit_spark_ethena_execution.py', 'audit_spark_par_swaps.py', 'audit_spark_susds_swaps.py'))]
    if args.legacy_curve_events:
        dependencies.append(Path(__file__).with_name('audit_spark_legacy_curve_swaps.py'))
        result['legacy_curve_summary'] = legacy_summary
    result['input_hashes'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in [*inputs, *dependencies]}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, indent=2))


if __name__ == '__main__':
    main()
