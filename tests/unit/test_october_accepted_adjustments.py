"""Operator-approved CoF and partner rebates use the existing MSC bridge once."""
import json
from decimal import Decimal as D
from pathlib import Path

import yaml

from settle.compute.sky_total_accrual import compute_sky_total_accrual
from settle.domain.period import Month

ROOT = Path(__file__).resolve().parents[2]


def test_partner_receipts_and_accepted_allocations_balance():
    rebates = json.loads((ROOT / 'reconciliation/2026-10/partner_rebates.json').read_text())
    assert rebates['status'] == 'operator_approved'
    assert sum((D(r['amount']) for r in rebates['receipts']), D(0)) == D('2057003.00')
    assert D(rebates['sky']['total']) == D('1141145.80')
    assert D(rebates['grove']['total']) == D('915857.20')
    assert D(rebates['sky']['total']) + D(rebates['grove']['total']) == D(rebates['total_received'])
    assert len({r['tx'] for r in rebates['receipts']}) == 6


def test_accepted_adjustments_change_mint_and_send_through_existing_preview(tmp_path):
    config = yaml.safe_load((ROOT / 'config/sky_total.yaml').read_text())
    cof = json.loads((ROOT / 'reconciliation/2026-10/sde_inflight_cof.json').read_text())
    rebates = json.loads((ROOT / 'reconciliation/2026-10/partner_rebates.json').read_text())
    adj = config['msc_preview']['2026-10']['grove']
    historical = json.loads((ROOT / 'reconciliation/2026-01_to_2026-09/data.json').read_text())['adjustment_totals']['grove']
    assert D(adj['sky_adj']) == D(historical['sky_adj']) + D(rebates['sky']['total'])
    assert D(adj['sv_adj']) == D(rebates['grove']['total']) + D(historical['sv_adj'])
    assert str(config['msc_preview']['2026-09']['grove']['sky_adj']) == '-165013.9'
    assert cof['months']['2026-08']['already_recorded_credit'] == '2508.55'
    prime = tmp_path / 'settlements/grove/2026-10'
    nonmsc = tmp_path / 'settlements/non_msc/2026-10'
    prime.mkdir(parents=True)
    nonmsc.mkdir(parents=True)
    (prime / 'provenance.json').write_text(json.dumps({'results': {
        'sky_revenue':'400000', 'sde_revenue':'200000', 'prime_agent_revenue':'1000000',
        'agent_rate':'1000', 'distribution_rewards':'0', 'chronicle_points':'0', 'gar':'0'}}))
    (nonmsc / 'provenance.json').write_text(json.dumps({'results': {'total_income':'0', 'total_expense':'0'}}))
    cfg = {'accrual_primes':['grove'], 'allocator_ilks':{'grove':'x'}, 'msc_preview':{}}
    before = compute_sky_total_accrual(Month(2026,10), repo_root=tmp_path, config=cfg).rows[0]
    cfg['msc_preview'] = {'2026-10':{'grove':adj}}
    after = compute_sky_total_accrual(Month(2026,10), repo_root=tmp_path, config=cfg).rows[0]
    assert after.derived_mint - before.derived_mint == D('1958138.18')
    assert after.derived_send - before.derived_send == D('927411.94')
    assert (after.derived_mint - after.derived_send) - (before.derived_mint - before.derived_send) == D('1030726.24')
