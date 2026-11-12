"""Build MSC #14 from reconciled prime reports and retained treasury results.

Reads tracked reports/preview, historical ledger, rebate and treasury output.
"""
from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_UP
from decimal import Decimal as D
from pathlib import Path

import yaml
from build_forum_post import _require, parse_rows, read_prime
from calculate_october_partner_rebate import replacements

ROOT = Path(__file__).resolve().parents[1]


def treasury_section(indexed):
    folder = ROOT / 'reconciliation/2026-10/treasury'
    manifest = json.loads((folder / 'manifest.json').read_text())
    for relative, digest in manifest['sha256'].items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != digest:
            raise ValueError('Stale treasury evidence: ' + relative)
    sky = json.loads((folder / 'sky-total.json').read_text())['results']
    tmf = json.loads((folder / 'tmf.json').read_text())
    non = json.loads((folder / 'non-msc.json').read_text())['results']
    for name, row in indexed.items():
        for field, key in [('mint', 'msc_mint'), ('send', 'msc_send')]:
            if D(sky['per_prime'][name][field]) != D(row[key]):
                raise ValueError('Treasury differs from prime preview: ' + name)
    if set(sky['per_prime']) != set(indexed):
        raise ValueError('Treasury prime coverage differs')
    income, expense = D(non['total_income']), D(non['total_expense'])
    snr = D(sky['sky_net_revenue'])
    if income - expense != D(sky['non_msc_net']) or D(sky['msc_net']) + income - expense != snr:
        raise ValueError('Consolidated revenue does not reconcile')
    if D(tmf['inputs']['snr']) != snr.quantize(D(1), rounding=ROUND_HALF_UP):
        raise ValueError('TMF uses stale Sky net revenue')
    w = tmf['results']['waterfall']
    lines = ['---', '', '## Sky Treasury Management Function Calculations', '',
             'October settlement totals include Reconciliation IV once. Treasury allocations below use '
             'the resulting consolidated Sky net revenue; they are proposed parameters, not executed transfers.', '',
             '| Revenue component | USDS |', '|---|---:|',
             f"| MSC net, including adjustments | {D(sky['msc_net']):,.2f} |",
             f'| Non-MSC income | {income:,.2f} |',
             f'| Non-MSC expense | -{expense:,.2f} |',
             f'| **Sky net revenue** | **{snr:,.2f}** |', '',
             f"TMF uses Sky net revenue rounded to {D(tmf['inputs']['snr']):,.0f} USDS.", '',
             '| TMF allocation / parameter | Value |', '|---|---:|']
    for label, key, unit, places in [
        ('Step 1 — Core Council Buffer (Core Council + Fortification)', 'step1_total', 'USDS', 2),
        ('Step 2 — retained in surplus buffer', 'step2_retained', 'USDS', 2),
        ('Step 3 — monthly engine budget', 'step3_capital', 'USDS', 2),
        ('Monthly USDS staking rewards', 'usds_rewards_usds', 'USDS', 2),
        ('Monthly SKY staking rewards', 'sky_rewards_monthly', 'SKY', 2),
        ('Burn purchase budget', 'burn_usds', 'USDS', 2),
        ('Splitter hop / USDS rewards duration', 'hop', 'seconds', 0),
        ('90-day SKY vest stream total', 'vest_tot', 'SKY', 0),
    ]:
        lines.append(f'| {label} | {D(w[key]):,.{places}f} {unit} |')
    lines += [f"| SKY to burn from October purchases | {D(tmf['results']['sky_to_burn']):,.2f} SKY |", '',
              'The engine uses 6,000 USDS per batch and a 55% buyback share. '
              'Monthly SKY rewards use actual October burn-window purchases allocated to stakers (45/55); the 90-day stream covers three months of those rewards. '
              'The burn above is attributed to actual October purchases, separately from the future burn purchase budget.', '',
              'Calculation details: [non-MSC](../settlements/non_msc/2026-10/summary.md), '
              '[consolidated Sky revenue](../settlements/sky_total/2026-10/summary.md), '
              '[TMF](../settlements/tmf/2026-10/summary.md).', '',
              'Independent daily asset checks remain explicitly deferred; ALM transfer reviews are retained.', '']
    return lines


def build():
    folder = ROOT / 'reconciliation/2026-10'
    preview = json.loads((folder / 'prime_msc_preview.json').read_text())
    ledger = json.loads((ROOT / 'reconciliation/2026-01_to_2026-09/data.json').read_text())
    rebate = json.loads((folder / 'partner_rebates.json').read_text())
    expected = replacements(ledger)
    cfg = yaml.safe_load((ROOT / 'config/forum_post.yaml').read_text())
    settlement = yaml.safe_load((ROOT / 'config/sky_total.yaml').read_text())
    indexed = {r['prime']: r for r in preview['rows']}
    agents = [p for g in cfg['executor_agents'] for p in g['primes']]
    if len(agents) != len(set(agents)) or set(agents) != set(indexed) or len(indexed) != len(preview['rows']):
        raise ValueError('Incomplete or duplicated Prime coverage')
    if set(agents) != set(settlement['accrual_primes']):
        raise ValueError('Prime set differs from settlement configuration')
    digest = hashlib.sha256((ROOT / 'config/sky_total.yaml').read_bytes()).hexdigest()
    if preview['sky_total_config_sha256'] != digest:
        raise ValueError('Preview configuration is stale')
    lines = ['# MSC #14 - Settlement Summary (October 2026)', '',
             '> Draft settlement instructions and TMF recommendations; execution remains subject to approval.', '',
             '## Methodology', '',
             'Demand-side revenue covers incentives and treasury income. Supply-side revenue covers allocation returns, borrowing costs and Sky Direct Exposures.', '',
             'October-earned revenue is shown separately from adjustments. Instructions include '
             '[Reconciliation IV](reconciliation-2026-01-to-2026-09.md) once, together with the other adjustments shown below. '
             'Final mint and transfer amounts are rounded to whole USDS after combining full-precision components. '
             'Potential discrepancies can be corrected in a subsequent reconciliation.', '',
             'Sources: [October reports](../settlements), '
             '[reconciliation ledger](../reconciliation/2026-01_to_2026-09/data.json) and '
             '[instruction preview](../reconciliation/2026-10/prime_msc_preview.json). '
             'No payment has been executed by this calculation.', '']
    labels = {'dv_adj': 'Demand-side correction', 'sv_adj': 'Prime supply-side correction',
              'sky_adj': 'Sky supply-side correction', 'send_credit': 'Send-only refund'}
    for group in cfg['executor_agents']:
        lines += ['---', '', '## ' + group['name'], '', group['foreword'], '']
        for name in group['primes']:
            row = indexed[name]
            path = ROOT / 'settlements' / name / '2026-10/summary.md'
            if hashlib.sha256(path.read_bytes()).hexdigest() != row['summary_sha256']:
                raise ValueError('Stale summary: ' + name)
            prime = read_prime(name, '2026-10', cfg)
            parsed = parse_rows(path)
            adjustments = settlement['msc_preview']['2026-10'].get(name, {})
            for field, preview_field in [('sv_adj', 'supply_adjustment'), ('sky_adj', 'sky_adjustment'),
                                         ('dv_adj', 'demand_adjustment'), ('send_credit', 'send_credit')]:
                amount = D(adjustments.get(field, '0'))
                if amount != D(row[preview_field]) or amount != D(expected.get(name, {}).get(field, '0')):
                    raise ValueError('Adjustment differs from reconciliation: ' + name)
            for report_value, key in [(prime.demand_total, 'demand_side_revenue'),
                                      (prime.prime_share, 'ordinary_supply_net'), (prime.sky_share, 'sky_ordinary_total')]:
                if abs(report_value - D(row[key])) > D('.005'):
                    raise ValueError('Ordinary report differs from preview: ' + name)
            # Independently apply production policy, keeping historical debits signed.
            supply = max(D(row['ordinary_supply_net']), D(0)) if prime.ilk else D(row['ordinary_supply_net'])
            supply += D(row['supply_adjustment'])
            mint = D(row['sky_ordinary_total']) + D(row['sky_adjustment']) + max(supply, D(0)) if prime.ilk else D(0)
            send = D(row['demand_side_revenue']) + D(row['demand_adjustment']) + supply + D(row['send_credit'])
            for label, value, key in [('Mint USDS (whole tokens)', mint, 'msc_mint'), ('Send USDS (whole tokens)', send, 'msc_send')]:
                rounded = value.quantize(D(1), rounding=ROUND_HALF_UP)
                reported = _require(parsed, label, 'MSC settlement instructions — October 2026', path=path)
                if rounded != D(row[key]) or rounded != reported:
                    raise ValueError('Instruction mismatch: ' + name)
            lines += [f'### {prime.label} Settlement for October 2026', '', '#### Demand Side Primitives', '']
            lines += ['- ' + c for c in prime.demand_components]
            lines += ['', f'**Demand Side Total:** {prime.demand_total:,.2f} USDS', '', '#### Supply Side Primitives', '']
            if prime.ilk:
                lines += ['- Allocation System Primitive', '', '**October-earned Supply Side Total**', '',
                          f'- **{prime.label} Share:** {prime.prime_share:,.2f} USDS', f'- **Sky Share:** {prime.sky_share:,.2f} USDS', '']
                if prime.prime_share < 0:
                    lines += ['The negative operating share remains visible above. Because the full borrowing charge is debt-financed, it is not deducted again from the demand-side payment.', '']
            else:
                lines += ['- No active Supply Side Primitives.', '']
            history = ledger['adjustment_totals'].get(name, {})
            if any(D(x) for x in history.values()) or name == 'grove':
                lines += ['#### Adjustments', '']
                for field, label in labels.items():
                    amount = D(history.get(field, '0'))
                    if amount:
                        lines += [f'- Reconciliation IV — {label}: {amount:+,.2f} USDS.']
                if name == 'grove':
                    lines += [f"- **Partner Rebate:** {D(rebate['grove']['total']):,.2f} USDS to Grove; {D(rebate['sky']['total']):,.2f} USDS to Sky."]
                lines += ['', 'These adjustments are already included in the instructions below.', '']
            lines += [f'#### {prime.label} Settlement', '']
            if prime.ilk:
                lines += [f"- Mint `{D(row['msc_mint']):,.0f} USDS` debt in `{prime.ilk}` and transfer the amount to the surplus buffer."]
            lines += [f"- Send `{D(row['msc_send']):,.0f} USDS` from the surplus buffer to the {prime.label} Subproxy [{prime.subproxy}](https://etherscan.io/address/{prime.subproxy}).", '']
    lines += treasury_section(indexed)
    return '\n'.join(lines)


if __name__ == '__main__':
    (ROOT / 'forum_posts/msc-2026-10.md').write_text(build())
