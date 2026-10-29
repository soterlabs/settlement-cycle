"""Prove fresh borrowing in selected USDC payments through USDS/DAI and PSM.

Exact cash paths only: no inference about activity inside the escrow EOA,
and no change to the accepted MSC principal-return treatment.
"""
from decimal import Decimal as D
from decimal import localcontext

from audit_spark_ordered_draws import (
    ALM,
    BUFFER,
    USDS,
    WAD,
    address,
    authenticate_draw,
    index,
    savings_receipts,
)

from settle.extract.transfer_logs import TRANSFER_TOPIC0

DAI = '0x6b175474e89094c44da98b954eedeac495271d0f'
USDC = '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
CONVERTER = '0x3225737a9bbb6473cb4a45b7244aca2befdb276a'
PSM = '0xf6e72db5454dd049d0788e411b06cfaf16853042'
POCKET = '0x37305b1cd40574e4c5ce33f8e8306be057fd7341'
ZERO = '0x' + '0' * 40


def prove_payments(batch, receipt, destinations, *, savings_specs=None):
    with localcontext() as ctx:
        ctx.prec = 80
        logs, drawn, wiped, positive, _negative, draws, _wipes = authenticate_draw(batch, receipt)
        ms = {m.account: m for m in batch.movements}
        if len(ms) != len(batch.movements):
            raise ValueError('Duplicate cash account movement')
        for token in (USDS.split(':')[-1], DAI, USDC):
            cash = ms.get('ethereum:' + ALM + ':' + token)
            if cash is None or cash.value_before < 0 or cash.external_income or cash.preserve_basis:
                raise ValueError('Requires ordinary cash balances')

        def transfers(token):
            return [log for log in logs if log['address'] == token and log['topics'][0] == TRANSFER_TOPIC0]

        def edge(rows, sender, recipient, raw):
            matches = [log for log in rows if address(log['topics'][1]) == sender
                       and address(log['topics'][2]) == recipient]
            if len(matches) != 1 or int(matches[0]['data'], 16) != raw:
                raise ValueError('Missing or mismatched conversion route edge')
            return matches[0]

        usds, dai, usdc = transfers(USDS.split(':')[-1]), transfers(DAI), transfers(USDC)
        wad = int(drawn * WAD)
        mint = edge(usds, ZERO, BUFFER, wad)
        incoming = edge(usds, BUFFER, ALM, wad)
        conversions = [log for log in usds if address(log['topics'][1]) == ALM
                       and address(log['topics'][2]) == CONVERTER]
        psm_payments = [log for log in dai if address(log['topics'][1]) == ALM
                        and address(log['topics'][2]) == PSM]
        if len(conversions) != 1 or len(psm_payments) != 1:
            raise ValueError('Requires single conversion and PSM purchase')
        conversion, psm_payment = conversions[0], psm_payments[0]
        converted = int(conversion['data'], 16)
        psm_wad = int(psm_payment['data'], 16)
        if psm_wad % 10**12:
            raise ValueError('PSM purchase not exact USDC amount')
        burn = edge(usds, CONVERTER, ZERO, converted)
        dai_mint = edge(dai, ZERO, ALM, converted)
        psm_return = edge(usdc, POCKET, ALM, psm_wad // 10**12)
        if (not index(positive[0]) < index(mint) < index(draws[0]) < index(incoming)
                or not index(conversion) < index(burn) < index(dai_mint)
                or not index(psm_payment) < index(psm_return)):
            raise ValueError('Facility conversion route out of order')
        # Track three actual token ledgers. Opening and unrelated receipts stay
        # unknown. Route outputs consume unknown first; every other payment
        # consumes known borrowing first. Thus no unrelated return is assumed
        # borrowed, even when it comes from a familiar savings position.
        tokens = {USDS.split(':')[-1]: WAD, DAI: WAD, USDC: D(10)**6}
        balances = {token: ms['ethereum:' + ALM + ':' + token].value_before for token in tokens}
        known = dict.fromkeys(tokens, D(0))
        converted_known = psm_known = D(0)
        payments = []
        for log in logs:
            token = log['address']
            if token not in tokens or log['topics'][0] != TRANSFER_TOPIC0:
                continue
            sender, recipient = address(log['topics'][1]), address(log['topics'][2])
            if ALM not in (sender, recipient):
                continue
            if sender == recipient:
                raise ValueError('Self-transfer requires separate route')
            value = D(int(log['data'], 16)) / tokens[token]
            if recipient == ALM:
                credit = (drawn if log == incoming else converted_known if log == dai_mint
                          else psm_known if log == psm_return else D(0))
                if credit > value:
                    raise ValueError('Converted borrowing exceeds received token amount')
                balances[token] += value
                known[token] += credit
            else:
                if value > balances[token]:
                    raise ValueError('Negative ordered token balance')
                target = token == USDC and recipient in destinations
                routed = log in (conversion, psm_payment) or target
                released = (max(D(0), value - (balances[token] - known[token])) if routed
                            else min(known[token], value))
                known[token] -= released
                balances[token] -= value
                if log == conversion:
                    converted_known = released
                elif log == psm_payment:
                    psm_known = released
                if target:
                    payments.append({'log_index': index(log), 'recipient': recipient,
                                     'amount': str(value), 'borrowed': str(released)})
        for token, balance in balances.items():
            cash = ms['ethereum:' + ALM + ':' + token]
            if balance != cash.value_before + cash.change:
                raise ValueError('Ordered token balance differs from normalized cash')
        return {'transaction': batch.identity, 'gross_draw_usds': str(drawn),
                'gross_wipe_usds': str(wiped), 'payments': payments,
                'savings_cash_received': savings_receipts(batch, logs, savings_specs)}
