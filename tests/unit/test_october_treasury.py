"""October treasury uses the reviewed prime legs and the current non-MSC close."""
import json
from decimal import Decimal as D
from pathlib import Path

from settle.compute.tmf import TmfInputs, TmfPolicy, compute_waterfall

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / 'reconciliation/2026-10/treasury'


def test_treasury_reconciles_and_does_not_add_historical_adjustments_twice():
    sky = json.loads((FOLDER / 'sky-total.json').read_text())['results']
    non = json.loads((FOLDER / 'non-msc.json').read_text())['results']
    preview = json.loads((ROOT / 'reconciliation/2026-10/prime_msc_preview.json').read_text())
    mint = sum(D(row['msc_mint']) for row in preview['rows'])
    send = sum(D(row['msc_send']) for row in preview['rows'])
    assert D(sky['total_mint']) == mint
    assert D(sky['total_send']) == send
    assert D(sky['msc_net']) == mint - send
    assert D(sky['sky_net_revenue']) == mint - send + D(non['total_income']) - D(non['total_expense'])
    assert D(sky['per_prime']['grove']['sky_adj']) == D('1135869.94')
    assert D(sky['per_prime']['grove']['sv_adj']) == D('822268.24')
    assert D(sky['per_prime']['osero']['send_credit']) == D('5514')


def test_tmf_waterfall_reproduces_saved_results():
    saved = json.loads((FOLDER / 'tmf.json').read_text())
    inputs = TmfInputs(**{k: v if k == 'month' else D(v) for k, v in saved['inputs'].items()})
    policy = TmfPolicy.from_config({'policy': saved['policy']})
    result = compute_waterfall(inputs, policy)
    for key, value in saved['results']['waterfall'].items():
        expected = getattr(result, key)
        assert expected == (value if isinstance(value, bool) else D(str(value))), key
    assert result.step1_total + result.step2_retained + result.step3_capital == inputs.snr
    assert result.sky_rewards_usds + result.usds_rewards_usds + result.burn_usds == result.step3_capital
    assert saved['pins']['partial'] is False
    assert saved['activity'] is not None


def test_reviewed_rwa_receipt_matches_income_without_fee_overlap():
    transfers = json.loads((FOLDER / 'rwa-jar-transfers.json').read_text())
    evidence = json.loads((FOLDER / 'rwa-jar-receipt.json').read_text())
    income = json.loads((FOLDER / 'non-msc.json').read_text())['results']
    assert evidence['receipt']['status'] == '0x1'
    assert evidence['transaction']['input'] == '0xac4c25b2'  # void()
    assert evidence['transaction']['to'] == '0x6c6d4be2223b5d202263515351034861dd9afdb6'
    assert len(transfers) == 1
    transfer = transfers[0]
    assert transfer['transaction_hash'] == evidence['transaction']['hash']
    assert int(transfer['topic2'], 16) == 0
    amount = D(int(transfer['data'], 16)) / D(10)**18
    assert amount == D(income['rwa_jar_void'])
    assert D(income['stability_fees_by_ilk'].get('RWA009-A', '0')) == 0
    vow_moves = [log for log in evidence['receipt']['logs']
                 if log['address'] == '0x35d1b3f3d7966a1dfe207aa4514c12a259a0492b'
                 and log['topics'][0].startswith('0xbb35783b')
                 and log['topics'][2].endswith('a950524441892a31ebddf91d3ceefa04bf454466')]
    assert sum(D(int(log['topics'][3], 16)) / D(10)**45 for log in vow_moves) == amount
