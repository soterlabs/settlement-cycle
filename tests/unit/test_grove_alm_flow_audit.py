import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.audit_grove_alm_flows import (
    COINBASE_PAYER,
    SPAM,
    USDC_ETH,
    USDC_MONAD,
    audit,
    classify,
    transfers,
    validate_pages,
)
from settle.extract.transfer_logs import TRANSFER_TOPIC0

HOLDER = '0x' + '11' * 20
OTHER = '0x' + '22' * 20
TOKEN = '0x' + '33' * 20


def capture():
    return {'scope': {'from_block': 10, 'to_block': 20, 'holders': [HOLDER]},
            'pages': [{'from_block': 10, 'next_block': 20, 'archive_height': 30}],
            'logs': [{'block_number': 12, 'log_index': 3, 'address': TOKEN,
                      'topic0': TRANSFER_TOPIC0,
                      'topic1': '0x' + OTHER[2:].rjust(64, '0'),
                      'topic2': '0x' + HOLDER[2:].rjust(64, '0'),
                      'topic3': None, 'transaction_hash': '0x' + '44' * 32,
                      'data': '0x' + hex(1000000)[2:].rjust(64, '0')}]}


@pytest.mark.parametrize('pages', [[],
    [{'from_block': 10, 'next_block': 19, 'archive_height': 30}],
    [{'from_block': 11, 'next_block': 20, 'archive_height': 30}],
    [{'from_block': 10, 'next_block': 10, 'archive_height': 30}],
    [{'from_block': 10, 'next_block': 20, 'archive_height': 18}],
])
def test_missing_or_gapped_capture_cannot_be_reported_as_zero(pages):
    with pytest.raises(ValueError):
        validate_pages(capture()['scope'], pages)


def test_event_identity_deduplicates_and_conflicts_fail():
    data = capture()
    data['logs'].append(deepcopy(data['logs'][0]))
    rows = transfers('ethereum', data, {('ethereum', TOKEN): ('USDC', 6)})
    assert len(rows) == 1 and rows[0]['amount'] == '1'
    data['logs'][1]['data'] = '0x' + '0' * 64
    with pytest.raises(ValueError, match='Conflicting'):
        transfers('ethereum', data, {})


def row(token=TOKEN, chain='ethereum'):
    return dict(chain=chain, token=token, sender=HOLDER, receiver=OTHER,
                transaction='0x' + '44' * 32, log_index=3,
                direction='out', symbol='unknown')


def status(value):
    return classify(value, income_routes={}, venue_tokens={},
                    bridge_events=set(), swap_events=set())[0]


def test_unknown_token_is_retained_not_automatically_spam():
    assert status(row()) == 'unresolved'


def test_counterfeit_and_real_usdc_cannot_share_classification():
    fake = next(t for c, t in SPAM if c == 'monad')
    assert status(row(fake, 'monad')) == 'excluded_unsolicited'
    assert status(row(USDC_MONAD, 'monad')) == 'unresolved_outflow'


def test_empty_erc20_amount_fails_instead_of_disappearing():
    data = capture()
    data['logs'][0]['data'] = '0x'
    with pytest.raises(ValueError, match='Malformed fungible'):
        transfers('ethereum', data, {})


def saved_capture(tmp_path):
    source = Path(__file__).parents[2] / 'reconciliation/grove_alm_flows_2026_10'
    target = tmp_path / 'capture'
    shutil.copytree(source, target)
    return target


def test_october_capture_balances_bridges_and_unresolved_cash(tmp_path):
    result = audit(saved_capture(tmp_path))
    assert result['transfer_events'] == 1850
    assert result['token_balance_controls'] == 16
    assert result['native_balance_controls'] == 6
    assert len(result['native_flows']) == 1
    assert result['status_counts'].get('unresolved_receipt', 0) == 0
    assert result['status_counts']['approved_partner_rebate'] == 6
    assert result['approved_partner_rebates']['grove']['total'] == '915857.20'
    assert result['status_counts']['unresolved_outflow'] == 2
    assert result['status_counts']['missing_venue'] == 200
    assert result['status_counts']['verified_bridge'] == 168


def test_changed_bridge_amount_cannot_be_certified(tmp_path):
    target = saved_capture(tmp_path)
    path = target / 'cctp-v2-proofs.json'
    data = json.loads(path.read_text())
    data['proofs'][0]['amount_usdc'] = '999999999'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='Bridge proof differs'):
        audit(target)


def test_shared_custodian_does_not_classify_unrelated_payments():
    value = {**row(USDC_ETH), 'sender': COINBASE_PAYER,
             'receiver': '0x491edfb0b8b608044e227225c715981a30f3a44e', 'amount': '12'}
    assert status(value) == 'unresolved_receipt'
    receipts = {value['transaction']: {'asset': 'JAAA', 'amount': '12'}}
    kwargs = dict(income_routes={}, venue_tokens={}, bridge_events=set(),
                  swap_events=set(), rebate_receipts=receipts)
    assert classify(value, **kwargs)[0] == 'approved_partner_rebate'
    with pytest.raises(ValueError, match='differs from actual receipt'):
        classify({**value, 'amount': '13'}, **kwargs)
