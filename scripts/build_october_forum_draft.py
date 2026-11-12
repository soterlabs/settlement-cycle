"""Build MSC #14 prime-leg draft; explicitly pending consolidated Sky/TMF results.

Unlike the complete-cycle publisher, this cannot claim a completed treasury
calculation. Reads tracked reports/preview, historical ledger and rebate output.
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
             '> Draft: Prime settlement instructions reviewed; consolidated Sky net revenue and TMF calculations are pending.', '',
             '## Methodology', '',
             'Demand-side revenue covers incentives and treasury income. Supply-side revenue covers allocation returns, borrowing costs and Sky Direct Exposures.', '',
             'October-earned revenue is shown separately from adjustments. Instructions include '
             '[Reconciliation IV](reconciliation-2026-01-to-2026-09.md) once, plus Partner Rebate. '
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
    lines += ['---', '', '## Sky Treasury Management Function Calculations', '',
              '**Pending:** the October consolidated Sky/non-MSC report and TMF calculations are not included in this draft. '
              'Prime mint-minus-transfer totals must not be presented as consolidated Sky net revenue.', '',
              'Independent daily asset checks remain explicitly deferred; ALM transfer reviews are retained. '
              'Complete the treasury section before publishing a full MSC #14 settlement summary.', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    (ROOT / 'forum_posts/msc-2026-10.md').write_text(build())
