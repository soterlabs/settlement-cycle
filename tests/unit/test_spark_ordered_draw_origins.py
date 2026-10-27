import gzip
import importlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_history_cache import load_history

ROOT = Path(__file__).resolve().parents[1] / 'fixtures'


@pytest.fixture(scope='module')
def data():
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(ROOT.parents[1] / 'scripts'))
        module = importlib.import_module('audit_spark_ordered_draws')
        h = load_history(ROOT / 'spark_susds_origin_history.jsonl.gz', 'spark-origin-slim')
        receipts = json.load(gzip.open(ROOT / 'spark_susds_ordered_draw_receipts.json.gz', 'rt'))
        yield module, h, receipts


def batch_for(h, tx):
    return next(b for b in h.batches if b.identity == 'ethereum:' + tx)


def test_gross_draw_survives_later_repayment(data):
    module, h, receipts = data
    tx = '0x0b1906522d76ba1778c59c36d26b600f2923acc6c9c1bfcf52818c1c09067c7f'
    proof = module.validate(batch_for(h, tx), receipts[tx])
    assert D(proof['gross_draw_usds']) == D('55844407.696565341730779568')
    assert D(proof['gross_wipe_usds']) == D('50204.059834')
    assert proof['verified_borrowed_contribution'] == proof['gross_draw_usds']
    assert D(proof['verified_borrowed_contribution']) > D(proof['normalized_net_debt'])


def test_actual_negative_net_debt_can_still_fund_a_new_position(data):
    module, h, receipts = data
    from audit_scoped_borrowing_origins import trace

    from settle.normalize.allocation_capital import CapitalHistory
    candidates = [b for b in h.batches if b.minted < 0 and b.identity.split(':')[1] in receipts]
    for b in candidates:
        try:
            proof = module.validate(b, receipts[b.identity.split(':')[1]])
        except ValueError:
            continue
        if D(proof['verified_borrowed_contribution']) > 0:
            break
    else:
        pytest.fail('Missing real gross-draw/net-repayment regression witness')
    mini = CapitalHistory((b,), {'S32': module.SUSDS}, {})
    before = trace(mini, ('S32',), b.day)
    after = trace(mini, ('S32',), b.day,
                  verified_draws={b.identity: D(proof['verified_borrowed_contribution'])})
    assert D(before['closing_borrowed_by_account'][module.SUSDS]) == 0
    assert D(after['closing_borrowed_by_account'][module.SUSDS]) > 0
    assert mini.batches[0].minted == b.minted < 0


@pytest.mark.parametrize('mutation', ['draw_amount','wrong_vault','missing_frob','wrong_savings_take','prefix_cash','duplicate_log'])
def test_ordered_route_rejects_changed_evidence(data, mutation):
    module, h, receipts = data
    tx = '0x0b1906522d76ba1778c59c36d26b600f2923acc6c9c1bfcf52818c1c09067c7f'
    b = batch_for(h, tx)
    receipt = deepcopy(receipts[tx])
    draw = next(log for log in receipt['logs'] if log['address'] == module.VAULT and log['topics'][0] == module.DRAW)
    if mutation == 'draw_amount':
        draw['data'] = hex(int(draw['data'], 16) + 10**18)
    elif mutation == 'wrong_vault':
        draw['address'] = module.ALM
    elif mutation == 'missing_frob':
        receipt['logs'] = [log for log in receipt['logs'] if not (
            log['address'] == module._VAT and log['topics'][0] == module._FROB_T0)]
    elif mutation == 'wrong_savings_take':
        take = next(log for log in receipt['logs'] if log['topics'][0] == module.TAKE)
        take['data'] = hex(int(take['data'], 16) + 1)
    elif mutation == 'prefix_cash':
        # Move the real later non-Buffer USDS ingress ahead of the deposit.
        ingress = next(log for log in receipt['logs'] if log['address'] == module.USDS.split(':')[-1]
                       and log['topics'][0] == module.TRANSFER_TOPIC0
                       and module.address(log['topics'][2]) == module.ALM
                       and module.address(log['topics'][1]) != module.BUFFER)
        ingress['logIndex'] = hex(min(module.index(log) for log in receipt['logs']) - 1)
    else:
        receipt['logs'].append(deepcopy(receipt['logs'][0]))
    with pytest.raises(ValueError):
        module.validate(b, receipt)


def test_opening_cash_and_forwarded_shares_never_inflate_origin(data):
    module, h, receipts = data
    for tx in ['0x9cea9ad16aec8e011097820067090e2416ee230fc6d1d3a3e021aa4bd6e051ed',
               '0x11264ba7a19e1f8d3e568bbc080698511ca7f8f0416a68a395126d88f82ece62']:
        proof = module.validate(batch_for(h, tx), receipts[tx])
        assert D(proof['verified_borrowed_contribution']) < D(proof['usds_deposited'])
        assert D(proof['verified_borrowed_contribution']) <= D(proof['gross_draw_usds'])


def test_verified_contribution_cannot_exceed_position_or_escape_scope(data):
    _module, h, _receipts = data
    from audit_scoped_borrowing_origins import trace
    tx = '0x0b1906522d76ba1778c59c36d26b600f2923acc6c9c1bfcf52818c1c09067c7f'
    b = batch_for(h, tx)
    mini = replace(h, batches=(b,))
    with pytest.raises(ValueError, match='exceeds'):
        trace(mini, ('S32',), b.day, verified_draws={b.identity: D('1e12')})
    with pytest.raises(ValueError, match='missing'):
        trace(mini, ('S32',), b.day, verified_draws={'absent': D(1)})
    with pytest.raises(ValueError, match='one account'):
        trace(mini, ('S32','S31'), b.day, verified_draws={b.identity: D(1)})


def test_complete_receipt_audit_preserves_history_and_reconciles_closing_capital(data):
    _module, h, receipts = data
    from audit_spark_savings_origin import attribute
    saved = h.batches
    result = attribute(h, receipts, date(2026,1,1), date(2026,8,31))
    assert h.batches == saved
    assert D(result['closing_capital']).quantize(D('.01')) == D('703133724.15')
    assert D(result['closing_borrowed']).quantize(D('.01')) == D('617587857.46')
    assert D(result['remaining_origin_to_classify']).quantize(D('.01')) == D('85545866.69')
    assert len(result['verified_routes']) == 107
    assert len(result['rejected_routes']) == 19
    assert abs(sum(D(row['unclassified_closing_capital']) for row in result['surviving_unclassified_sources'])
               - D(result['remaining_origin_to_classify'])) < D('1e-8')
    # An already-proven draw is replaced, never added again on replay.
    assert attribute(h, receipts, date(2026,1,1), date(2026,8,31))['closing_borrowed'] == result['closing_borrowed']


def test_mixed_prefix_attributes_only_draw_not_other_cash(data):
    module, h, receipts = data
    tx = '0x90dce95b9d809344872e6a830feb6c7aa4e4fcd471c36f4e264979e7fd90bb7d'
    b = batch_for(h, tx)
    with pytest.raises(ValueError, match='contaminates'):
        module.validate(b, receipts[tx])
    proof = module.validate(b, receipts[tx], allow_mixed_prefix=True)
    assert D(proof['verified_borrowed_contribution']) == D('53983649.231941332839436392')
    assert D(proof['additional_unclassified_usds']) == D('156434.075182905649945882')
    assert D(proof['usds_deposited']) > D(proof['verified_borrowed_contribution'])


def test_mixed_prefix_still_rejects_intervening_cash_payment(data):
    module, h, receipts = data
    tx = '0x90dce95b9d809344872e6a830feb6c7aa4e4fcd471c36f4e264979e7fd90bb7d'
    receipt = deepcopy(receipts[tx])
    extra = next(log for log in receipt['logs'] if module.index(log) == 165)
    extra['topics'][1], extra['topics'][2] = extra['topics'][2], extra['topics'][1]
    with pytest.raises(ValueError, match='contaminates'):
        module.validate(batch_for(h, tx), receipt, allow_mixed_prefix=True)
