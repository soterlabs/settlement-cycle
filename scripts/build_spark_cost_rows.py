"""Add explicit PSM rows and deduction credits to saved conditional cost results."""
import argparse
import gzip
import hashlib
import json
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from audit_saved_funding_gap import check, decompose, read

from settle.compute.allocation_cost_rows import (
    cost_row,
    deduction_credit,
    excess_deduction_credit,
    psm_cost_rows,
)


def build(control, funding, conditional, accounts, vat, idle, header, comparison, routes):
    bridge = decompose(control, funding, conditional, accounts, vat)
    with localcontext() as ctx:
        ctx.prec = 60
        days = {r['date']: r for r in control['sky_revenue_daily']}
        rates = {d: D(r['daily_sky_rev']) / D(r['utilized']) for d, r in days.items()}
        sde = {v['venue_id']: {r['block_date']: D(r['cum_value']) for r in v['daily']}
               for v in control['sde_daily_breakdown']}
        if any(set(v) != set(days) for v in sde.values()):
            raise ValueError('Incomplete SDE control')
        rows = psm_cost_rows(accounts, rates, selected_accounts=funding['identity']['accounts'])
        check(sum((r['cost_of_funds'] for r in rows), D(0)), bridge['account_groups']['psm'], 'PSM gross')
        rows.append(deduction_credit('psm:idle_usds', {d: D(r['psm_usds']) for d, r in days.items()},
                                     rates, scope='psm_all_chains'))
        rows.append(deduction_credit('psm:usdc_sde',
            {d: D(r['sde_av']) - sum((v[d] for v in sde.values()), D(0)) for d, r in days.items()},
            rates, scope='psm_all_chains'))
        for v in conditional['allocations']:
            if v['conditional_sky_borrowing_cost'] is None:
                continue
            deductions = {d: D(idle['daily'].get(v['venue'], {}).get(d, 0)) + sde.get(v['venue'], {}).get(d, D(0)) for d in days}
            row = excess_deduction_credit('excess:' + v['venue'],
                {d: D(v['daily'][d]['sky_principal']) for d in days}, deductions, rates, scope=v['venue'])
            if row['cost_of_funds']:
                rows.append(row)
        observed = {d: D(0) for d in days}
        for a in header['idle_accounts']:
            principal = {d: D(funding['daily_funding'][d][a]['sky_principal']) for d in days}
            value = {d: D(funding['daily_funding'][d][a]['observed_value']) for d in days}
            for d in days:
                observed[d] += value[d]
            # The existing raw-USDS row excludes its entire Sky-funded basis.
            # Preserve the exact signed difference even if basis exceeds value.
            row = cost_row('idle_usds_adjustment:' + a, 'deduction_adjustment',
                {d: (principal[d] - value[d]) * rates[d] for d in days}, scope=a,
                basis='global_observed_idle_value_less_previously_excluded_sky_basis')
            if row['cost_of_funds']:
                rows.append(row)
        for d in days:
            check(observed[d], D(days[d]['alm_usds']), d + ' raw USDS control')
        subtotal = bridge['conditional_allocation_cost'] + sum((r['cost_of_funds'] for r in rows), D(0))
        # No residual plug: retain non-investment financing explicitly and show
        # what remains. Identified RLUSD routes are still NOT corrected costs.
        financing = {'msc': D(comparison['msc_financing_cost']),
                     'vat_accrual': D(comparison['vat_accrual_financing_cost']),
                     'savings_interest_basis': bridge['account_groups']['financing']}
        global_cost = sum((D(r['daily_sky_rev']) for r in days.values()), D(0))
        check(global_cost, D(comparison['global_cost']), 'global cost')
        unassigned = global_cost - subtotal - sum(financing.values(), D(0))
        pending_routes = D(routes['associated_residual_cost'])
        if not pending_routes.is_finite() or pending_routes < 0 or pending_routes > unassigned:
            raise ValueError('Identified route costs must be finite and within unassigned cost')
        expected = (bridge['account_groups']['unallocated'] + bridge['account_groups']['cctp']
                    + bridge['account_groups']['rounding'] + bridge['components']['unpriced_selected']
                    + bridge['components']['cash_debt_gap'])
        check(unassigned, expected, 'unassigned cost after explicit credits')
        return {'publishable': False, 'start': conditional['start'], 'end': conditional['end'],
            'policy': 'Existing venue costs remain unchanged; explicit custody rows and signed credits. No redistributed credits.',
            'existing_conditional_allocation_cost': bridge['conditional_allocation_cost'],
            'additional_cost_rows': rows, 'allocation_and_custody_cost_including_credits': subtotal,
            'separate_financing_costs': financing, 'global_cost': global_cost,
            'unassigned_cost': unassigned, 'identified_rlusd_routes_pending_replay': pending_routes,
            'remaining_after_identified_routes': unassigned - pending_routes,
            'per_chain_psm_net_cost_available': False,
            'limitations': ['Shared PSM deductions have no verified per-chain split in these saved controls.',
                'Conditional funding and Savings-interest basis remain subject to attribution review.',
                'RLUSD deposit costs remain in unassigned cost until receipt-position replay is corrected.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--saved-dir', type=Path, required=True)
    p.add_argument('--control', type=Path, required=True)
    p.add_argument('--idle', type=Path, required=True)
    p.add_argument('--routes', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    root = args.saved_dir
    paths = [args.control, *[root/n for n in ('funding-snapshot.json.gz', 'conditional-costs.json.gz',
        'daily-by-ilk.json.gz', 'vat-accrual.json')], args.idle]
    with gzip.open(root/'funded-history.jsonl.gz', 'rt') as f:
        header = json.loads(next(f))
    comparison_path = root/'global-comparison.json'
    result = build(*[read(path) for path in paths], header, read(comparison_path), read(args.routes))
    result['input_sha256'] = {}
    for path in [*paths, root/'funded-history.jsonl.gz', comparison_path, args.routes]:
        with path.open('rb') as f:
            result['input_sha256'][str(path)] = hashlib.file_digest(f, 'sha256').hexdigest()
    args.output.write_text(json.dumps(result, default=str, indent=2) + '\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('additional_cost_rows','input_sha256')}, default=str, indent=2))


if __name__ == '__main__':
    main()
