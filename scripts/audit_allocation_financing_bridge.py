#!/usr/bin/env python3
"""Read-only numerical bridge, including unresolved modeled allocation costs.

This deliberately does NOT certify funding provenance or change eligible costs.
A zero unexplained difference can coexist with unresolved receipts and allocations.
"""
import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

from settle.compute.allocation_capital import replay_history
from settle.compute.executed_spell_capital import apply_executed_spells
from settle.normalize.allocation_history_cache import load_history

ZERO = D(0)


def numerical_bridge(history, replay, finance, control, debt_days, idle=None):
    debt = {r['day']: r['by_ilk'] for r in debt_days}
    ownership = {(r['day'], r['ilk']): r for r in finance['per_ilk_reconciliation']['daily']}
    modeled_rows = [r for r in finance['allocations'] if 'modeled_cost_of_funds' in r]
    venue_config = {r['venue_id']: r for r in control['venue_breakdown']}
    sde = {r['venue_id']: {d['block_date']: d for d in r['daily']}
           for r in control.get('sde_daily_breakdown', [])}
    represented = set()
    for r in modeled_rows:
        v = r['venue_id']
        if v in history.venue_accounts:
            represented.add(history.venue_accounts[v])
        represented.update(history.custody_accounts.get(v, []))
    totals = defaultdict(lambda: defaultdict(D))
    outside = defaultdict(lambda: defaultdict(D))
    batches = iter(sorted(history.batches, key=lambda b: (b.day, b.timestamp)))
    pending = next(batches, None)
    observed_debt = defaultdict(D)
    for row in control['sky_revenue_daily']:
        day = date.fromisoformat(row['date'])
        while pending is not None and pending.day <= day:
            for ilk, amount in (pending.minted_by_ilk or {'unattributed': pending.minted}).items():
                observed_debt[ilk] += amount
            pending = next(batches, None)
        utilized = D(row['utilized'])
        rate = D(row['daily_sky_rev'])/utilized if utilized else D(str(row['base_apr']))/365
        modeled_deductions = defaultdict(D)
        for allocation in modeled_rows:
            vid = allocation['venue_id']
            account = history.venue_accounts.get(vid)
            accounts = ([account] if account else []) + history.custody_accounts.get(vid, [])
            origins = defaultdict(D)
            for a in accounts:
                for ilk, amount in replay.daily_by_ilk[day].get(a, {}).items():
                    origins[ilk] += amount
            principal = sum(origins.values(), ZERO)
            cfg = venue_config.get(vid, {})
            if cfg.get('cof_excluded') or account in history.idle_accounts:
                deduction = principal
            else:
                daily_sde = sde.get(vid, {}).get(str(day))
                deduction = (D(str(daily_sde['cum_value'])) if daily_sde else
                             principal*D(str(cfg.get('sd_share', 0))))
                deduction += D(str((idle or {}).get(vid, {}).get(str(day), 0)))
            for ilk, amount in origins.items():
                modeled_deductions[ilk] += (deduction*amount/principal if principal else ZERO)*rate
        for raw_ilk, v in debt[str(day)].items():
            ilk = '0x'+raw_ilk.removeprefix('0x')
            nonmsc = D(v['debt'])-D(v['prior_msc_debt'])-D(v['current_month_msc_debt'])
            if abs(observed_debt[ilk]-nonmsc) > D('.01'):
                raise ValueError('Observed draws/repayments do not reproduce non-MSC debt')
            basis = known = ZERO
            for account, origins in replay.daily_by_ilk[day].items():
                amount = origins.get(ilk, ZERO)
                basis += amount
                if account in represented:
                    known += amount
                else:
                    category = account.split(':', 1)[0]
                    outside[ilk][category] += amount*rate
            totals[ilk]['represented_gross_cost'] += known*rate
            totals[ilk]['modeled_deduction_cost'] += modeled_deductions[ilk]
            totals[ilk]['debt_without_remaining_asset_basis_financing'] += (nonmsc-basis)*rate
            totals[ilk]['global_deduction_cost'] += D(str(ownership[(str(day),ilk)]['deductions']))*rate
    result = {}
    for ilk, t in totals.items():
        modeled = sum((D(str(r['modeled_cost_of_funds_by_ilk'].get(ilk, ZERO))) for r in modeled_rows), ZERO)
        target = D(str(finance['per_ilk_reconciliation']['by_ilk'][ilk]['global_excluding_msc']))
        deduction_difference = t['modeled_deduction_cost']-t['global_deduction_cost']
        explained = modeled+sum(outside[ilk].values(), ZERO)+t['debt_without_remaining_asset_basis_financing']+deduction_difference
        result[ilk] = {'global_excluding_msc': target, 'modeled_allocation_cost': modeled,
            'outside_allocation_financing_by_account_type': dict(outside[ilk]),
            'debt_without_remaining_asset_basis_financing': t['debt_without_remaining_asset_basis_financing'],
            'deduction_difference_financing': deduction_difference,
            'modeled_cost_inconsistency': t['represented_gross_cost']-t['modeled_deduction_cost']-modeled,
            'unexplained_numerical_difference': target-explained,
            'numerical_bridge_within_one_cent': abs(target-explained) <= D('.01'),
            'eligible_allocation_cost': finance['per_ilk_reconciliation']['by_ilk'][ilk]['allocation_cost']}
    return {'scope': 'Conditional numerical bridge only. Unknown receipts remain unclassified; modeled costs are not certified.',
        'unknown_receipts': finance['unmatched_receipts'], 'by_ilk': result}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('history', 'financing', 'control', 'debt-control', 'idle-deductions', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    args = p.parse_args()
    finance = json.loads(args.financing.read_text())
    for path in (args.history, args.control, args.debt_control, args.idle_deductions):
        if finance['input_hashes'].get(str(path)) != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError('Bridge inputs differ from financing replay inputs')
    # An intentionally preserved normalized diagnostic snapshot. Its original
    # fingerprint identifies the saved extraction, not a fresh extraction on
    # current code. Current reviewed history adapters and ledger run afresh.
    with gzip.open(args.history, 'rt') as f:
        fingerprint = json.loads(next(f))['fingerprint']
    h = apply_executed_spells(load_history(args.history, fingerprint))
    control = json.loads(args.control.read_text())
    days = [date.fromisoformat(r['date']) for r in control['sky_revenue_daily']]
    replay = replay_history(h, min(days)-timedelta(days=1), max(days))
    if {k: D(str(v)) for k,v in finance['unmatched_receipts'].items()} != replay.unmatched_receipts:
        raise ValueError('Financing result is stale relative to the current replay')
    result = numerical_bridge(h, replay, finance, control, json.loads(args.debt_control.read_text()),
                              json.loads(args.idle_deductions.read_text())['daily'])
    result['input_hashes'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (args.history, args.financing, args.control, args.debt_control, args.idle_deductions)}
    result['realised_principal_loss_at_end'] = replay.ledger.realised_principal_loss
    args.output.write_text(json.dumps(result, indent=2, default=str)+'\n')


if __name__ == '__main__':
    main()
