#!/usr/bin/env python3
"""Compare allocation costs to a read-only published settlement control."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from dataclasses import fields
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from settle.compute.allocation_financing import allocation_financing
from settle.compute.allocation_reconciliation import validate_idle_control
from settle.compute.monthly_pnl import (
    _aggregate_curve_idle_usds,
    _aggregate_lending_idle_usds,
    _aggregate_univ4_idle_usds,
)
from settle.domain.config import load_prime_by_id
from settle.domain.monthly_pnl import VenueRevenue
from settle.domain.period import Period
from settle.domain.primes import Chain
from settle.normalize.allocation_history_cache import fingerprint, load_history
from settle.normalize.sources.hypersync_block_resolver import HyperSyncBlockResolver


def control_pnl(control):
    venues = []
    for original in control['venue_breakdown']:
        row = dict(original, tw_avg_value=original.get('tw_avg_value_usd', '0'),
                   tw_avg_notional=original.get('tw_avg_notional_usd', '0'))
        venues.append(VenueRevenue(**{
            f.name: Decimal(row[f.name]) if 'Decimal' in str(f.type) else row[f.name]
            for f in fields(VenueRevenue) if f.name in row}))
    return SimpleNamespace(
        period=Period(date.fromisoformat(control['period']['start']),
                      date.fromisoformat(control['period']['end']),
                      pin_blocks={Chain(c): b for c, b in control['pin_blocks_eom'].items()}),
        venue_breakdown=venues, sky_revenue_daily=control['sky_revenue_daily'],
        sde_daily_breakdown=[SimpleNamespace(venue_id=b['venue_id'], daily=[{
            'block_date': date.fromisoformat(r['block_date']), 'cum_value': Decimal(r['cum_value']),
            'uncapped_value': Decimal(r['uncapped_value'])} for r in b['daily']])
            for b in control.get('sde_daily_breakdown', [])],
        **{k: Decimal(control['results'].get(k, '0')) for k in
           ['sky_revenue', 'sde_revenue', 'susds_spread_reimbursement']})


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provenance', type=Path, required=True)
    parser.add_argument('--history-dir', type=Path, required=True)
    parser.add_argument('--capture-idle-only', action='store_true',
                        help='Persist exact daily deductions without replaying capital')
    parser.add_argument('--debt-control', type=Path,
                        help='Pinned per-ilk daily debt and MSC balances (JSON list)')
    parser.add_argument('--deduction-owners', type=Path,
                        help='Verified deduction field to 0x-prefixed ilk mapping (JSON object)')
    args = parser.parse_args()
    if bool(args.debt_control) != bool(args.deduction_owners):
        parser.error('--debt-control and --deduction-owners must be supplied together')
    original = args.provenance.read_bytes()
    control = json.loads(original)
    prime = load_prime_by_id(control['prime_id'])
    pnl = control_pnl(control)
    manifest = json.loads((args.history_dir / 'pins.json').read_text())
    if (manifest['prime'] != prime.id or manifest['start'] != str(pnl.period.start)
            or manifest['end'] != str(pnl.period.end)):
        parser.error('history and control must cover the same prime and period')
    pins = {Chain(c): b for c, b in manifest['pins'].items()}
    if any(pnl.period.pin_blocks.get(c) != b for c, b in pins.items()):
        parser.error('history and control have different block pins')
    history = None
    if not args.capture_idle_only:
        history = load_history(args.history_dir / 'history.jsonl.gz', fingerprint(prime, pins))
        if history is None:
            parser.error('normalized history is absent or stale; run validate_allocation_capital first')
    resolver = HyperSyncBlockResolver()
    idle = {}
    ssr = pd.DataFrame([{'effective_date': date.fromisoformat(r['date']),
                         'ssr_apy': Decimal(str(r['ssr_apy']))} for r in pnl.sky_revenue_daily])
    idle_path = args.history_dir / 'idle_deductions.json'
    control_hash = hashlib.sha256(original).hexdigest()
    idle_key = fingerprint(prime, pins)
    saved = json.loads(idle_path.read_text()) if idle_path.exists() else {}
    if saved.get('control_sha256') == control_hash and saved.get('fingerprint') == idle_key:
        idle = {vid: {date.fromisoformat(d): Decimal(v) for d, v in values.items()}
                for vid, values in saved['daily'].items()}
    else:
        _aggregate_curve_idle_usds(prime, pnl.period, curve_pool_source=None, block_resolver=resolver,
                                  ssr_history=ssr, capital_idle_amounts=idle)
        _aggregate_univ4_idle_usds(prime, pnl.period, v4_source=None, block_resolver=resolver,
                                 capital_idle_amounts=idle)
        _aggregate_lending_idle_usds(prime, pnl.period, block_resolver=resolver, capital_idle_amounts=idle)
        temporary = idle_path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'control_sha256': control_hash, 'fingerprint': idle_key,
            'daily': {vid: {str(d): str(v) for d, v in values.items()} for vid, values in idle.items()}}, indent=2))
        temporary.replace(idle_path)
    idle_exclusions = validate_idle_control(idle, control)
    if args.capture_idle_only:
        print(json.dumps({'idle_venues': sorted(idle), 'control_sha256': control_hash}))
        return
    result = allocation_financing(pnl, history, idle_amounts=idle)
    result['idle_control_excluded_venues'] = idle_exclusions
    result['control_sha256'] = hashlib.sha256(original).hexdigest()
    result['scope'] = 'ilk' if not prime.extra_ilks else 'prime_combined_ilks'
    result['ilks'] = ['0x' + i.hex() for i in [prime.ilk_bytes32, *prime.extra_ilks] if i]
    if args.debt_control:
        from settle.compute.allocation_reconciliation import reconcile_ilks

        debt_bytes = args.debt_control.read_bytes()
        owner_bytes = args.deduction_owners.read_bytes()
        result['per_ilk_reconciliation'] = reconcile_ilks(
            result, control, json.loads(debt_bytes), json.loads(owner_bytes))
        result['per_ilk_reconciliation']['input_hashes'] = {
            'debt_control': hashlib.sha256(debt_bytes).hexdigest(),
            'deduction_owners': hashlib.sha256(owner_bytes).hexdigest(),
        }
    # Never rewrite the control or existing settlement artifacts.
    output = args.history_dir / 'financing.json'
    if output.resolve() == args.provenance.resolve():
        parser.error('output must not overwrite the control')
    temporary = output.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(result, indent=2, default=str) + '\n')
    temporary.replace(output)
    if args.provenance.read_bytes() != original:
        raise RuntimeError('Settlement control changed during validation')
    print(json.dumps({k: result['reconciliation'][k] for k in
                     ['allocation_sum', 'existing_cost_of_funds', 'difference', 'complete',
                      'source_complete', 'within_one_cent']}, indent=2, default=str))


if __name__ == '__main__':
    main()
