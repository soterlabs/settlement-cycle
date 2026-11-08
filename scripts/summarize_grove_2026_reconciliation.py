#!/usr/bin/env python3
"""Summarize measured 2026 Grove corrections; not a completed annual audit or payment instruction."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from decimal import ROUND_HALF_UP
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_grove_september_2026 import EVIDENCE, audit  # noqa: E402
from scripts.reconcile_grove_buidl_fees import reconcile  # noqa: E402


def money(value):
    return str(value.quantize(D('.01'), rounding=ROUND_HALF_UP))


def redemption_by_venue(document):
    """Split only linear joint days; never invent a tranche-crossing attribution."""
    amounts = defaultdict(D)
    # The historical, approved replay used this subsidy tranche, not a
    # prospective cap from a future configuration change.
    historical_cap = D('1000000000')
    for row in document['daily']:
        changes = {vid: D(value) for vid, value in row['deduction_changes_by_venue'].items()}
        credit = D(row['credit_to_grove'])
        if len(changes) == 1:
            amounts[next(iter(changes))] += credit
            continue
        before, after = D(row['frozen_utilized']), D(row['corrected_utilized'])
        same_tranche = (min(before, after) >= historical_cap
                        or max(before, after) <= historical_cap)
        if not same_tranche or not all(value > 0 for value in changes.values()):
            raise ValueError('Joint CoF correction needs an explicit nonlinear attribution')
        # On the actual May 18/19 joint days both endpoints exceed the cap.
        # Every component therefore has the same marginal daily rate.
        total, remaining = sum(changes.values(), D(0)), credit
        for index, (vid, value) in enumerate(changes.items()):
            part = remaining if index == len(changes) - 1 else credit * value / total
            amounts[vid] += part
            remaining -= part
    for month, row in document['months'].items():
        prior = D(row['already_recorded_credit'])
        if prior:
            if month != '2026-08' or prior != D('2508.55'):
                raise ValueError('Prior CoF credit needs a reviewed venue allocation')
            amounts['E10'] -= prior
    if abs(sum(amounts.values(), D(0)) - D(document['additional_credit_unrounded'])) > D('1e-18'):
        raise ValueError('Per-venue credits do not reproduce the accepted CoF total')
    return dict(amounts)


def summarize():
    cof = json.loads((ROOT / 'reconciliation/2026-10/sde_inflight_cof.json').read_text())
    split = redemption_by_venue(cof)
    deposits = json.loads((ROOT / 'reconciliation/2026-10/async_deposit_cof.json').read_text())
    if any(row['venue'] != 'E9' for row in deposits['daily']):
        raise ValueError('Deposit correction needs a new venue breakdown')
    if abs(sum((D(r['credit_to_grove']) for r in deposits['daily']), D(0))
           - D(deposits['additional_credit_unrounded'])) > D('1e-18'):
        raise ValueError('Deposit daily corrections do not reconcile')
    fees = reconcile()
    holdings = audit(json.loads(EVIDENCE.read_text()))
    rows = {
        'E9': {'label': 'JTRSY', 'configured': split['E9'],
               'further': D(deposits['additional_credit_unrounded']), 'coverage': 'identified Jan-May CoF cases'},
        'E10': {'label': 'BUIDL', 'configured': split['E10'],
                'further': -D(fees['net_sky_adjustment_unrounded']),
                'coverage': 'May/Aug/Sep CoF and Jan-Aug small-share/cash-cost reconciliation'},
        'E8': {'label': 'JAAA', 'configured': split['E8'], 'further': D(0), 'coverage': 'March settlement-day CoF debit'},
        'E43': {'label': 'Base Grove x Steakhouse USDC', 'configured': D(0),
                'further': D(holdings['vaults']['E43']['revenue_existing_method']), 'coverage': 'September only'},
        'E44': {'label': 'Diamond PAU Uniswap', 'configured': D(0),
                'further': D(holdings['uniswap']['E44']['revenue']),
                'coverage': 'September only; earlier position history not quantified'},
        'E42': {'label': 'Warehouse principal schedule', 'configured': D(0),
                'further': D(0), 'coverage': 'September CoF redistribution only'},
        'E41': {'label': 'Basin reporting', 'configured': D(0),
                'further': D(0), 'coverage': 'Reporting redistribution only; existing September exemption unchanged'},
    }
    configured = sum((row['configured'] for row in rows.values()), D(0))
    further = sum((row['further'] for row in rows.values()), D(0))
    return {
        'status': 'partial_2026_measurements_not_full_year', 'sign': 'Positive benefits Grove; negative is a debit',
        'whole_year_total': None,
        'coverage_note': 'Identified corrections primarily through September. Unreviewed months and venues are not zero. No execution or extra off-report adjustments are asserted.',
        'venues': {vid: {'label': row['label'], 'coverage': row['coverage'],
                         'already_configured_in_october_bridge': money(row['configured']),
                         'further_measured_not_booked': money(row['further'])}
                   for vid, row in rows.items()},
        'already_configured_cof_total': money(configured),
        'further_measured_total': money(further),
        'measured_total_including_configured_cof': money(configured + further),
        'excluded': 'Earlier September BUIDL 165,013.90 credit; separately configured partner rebates (including 2025 earnings); normal monthly income and unquantified Robinhood/earlier LP receipts. Do not book configured CoF twice.',
    }


if __name__ == '__main__':
    print(json.dumps(summarize(), indent=2))
