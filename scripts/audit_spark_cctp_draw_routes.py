"""Prove fresh USDC borrowing paid into already authenticated CCTP v1 claims.

Message ownership and destination are supplied by the normalized CCTP links;
receipt nonce, emitter, token, owner, funding payment and burn must also agree.
This proves a source contribution, never a second allocation of bridge value.
"""
from decimal import Decimal as D

from audit_spark_usdc_draw_routes import ALM, USDC, ZERO, address, index, prove_payments

from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_bridges import DEPOSIT_FOR_BURN

# Ethereum Circle v1 TokenMinter; the normalized link extractor also validates
# each messenger's localMinter() before accepting its original burn payment.
MINTER = '0xc4922d64a24675e16e1586e3e3aa56c06fabe907'


def validate(batch, receipt, *, savings_specs=None):
    claims = {m.account: m for m in batch.movements if m.account.startswith('cctp:') and m.change > 0}
    if not claims or any(m.value_before or m.external_income for m in claims.values()):
        raise ValueError('Requires new authenticated CCTP v1 claims')
    proof = prove_payments(batch, receipt, {MINTER}, savings_specs=savings_specs)
    payments = {p['log_index']: p for p in proof['payments']}
    logs = sorted(receipt['logs'], key=index)
    used, linked = set(), []
    for log in logs:
        if log['topics'][0] != DEPOSIT_FOR_BURN:
            continue
        claim = f"cctp:0:{int(log['topics'][1], 16)}:{log['address']}"
        if claim not in claims:
            continue
        words = [int(log['data'][i:i+64], 16) for i in range(2, len(log['data']), 64)]
        if (len(words) != 5 or address(log['topics'][2]) != USDC
                or address(log['topics'][3]) != ALM
                or D(words[0]) / 10**6 != claims[claim].change):
            raise ValueError('CCTP event differs from normalized source claim')
        paid = next((p for n, p in sorted(payments.items()) if n not in used
                     and n < index(log) and D(p['amount']) == claims[claim].change), None)
        if paid is None:
            raise ValueError('CCTP claim lacks an unconsumed funding payment')
        burns = [event for event in logs if event['address'] == USDC
                 and event['topics'][0] == TRANSFER_TOPIC0
                 and address(event['topics'][1]) == MINTER and address(event['topics'][2]) == ZERO
                 and int(event['data'], 16) == words[0]
                 and paid['log_index'] < index(event) < index(log)]
        if len(burns) != 1 or any(row['claim'] == claim for row in linked):
            raise ValueError('Duplicate claim or ambiguous burn payment')
        used.add(paid['log_index'])
        linked.append({'claim': claim, 'amount': paid['amount'], 'borrowed': paid['borrowed'],
                       'payment_log_index': paid['log_index'], 'burn_log_index': index(burns[0])})
    if len(linked) != len(claims) or used != payments.keys():
        raise ValueError('Unmatched CCTP claims or TokenMinter payments')
    return {**proof, 'claims': linked}
