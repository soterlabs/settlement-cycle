"""Actual SDE liquidity subscriptions/redemptions, separate from AMM swaps.

V4 principal amounts reuse the saved event/receipt reconciliation, including
mixed swap/liquidity settlements. Curve cash legs are authenticated against LP
activity and canonical pool events. These are classifications, not new cash.
"""
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, localcontext

from ..extract._keccak import keccak256
from ..extract.transfer_logs import TRANSFER_TOPIC0
from .spark_onchain import topic
from .spark_v4_fees import capital_receipt
from .verification import digest
from ..extract.cache import cached

USDT = '0xdac17f958d2ee523a2206206994597c13d831ec7'
CURVE = '0x00836fe54625be242bcfa286207795405ca4fd10'
MANAGER = '0x000000000004444c5dc75cb358380d2e3de08a90'
ZERO = topic('0x' + '0' * 40)
SIGNATURES = {
    'AddLiquidity(address,uint256[2],uint256[2],uint256,uint256)': (1, 1),
    'RemoveLiquidity(address,uint256[2],uint256[2],uint256)': (-1, 1),
    'RemoveLiquidityImbalance(address,uint256[2],uint256[2],uint256,uint256)': (-1, 1),
}
DYNAMIC = {'0x' + keccak256(sig.encode()).hex(): sign for sig, sign in {
    'AddLiquidity(address,uint256[],uint256[],uint256,uint256)': 1,
    'RemoveLiquidity(address,uint256[],uint256[],uint256)': -1,
    'RemoveLiquidityImbalance(address,uint256[],uint256[],uint256,uint256)': -1,
}.items()}
TOPICS = {'0x' + keccak256(sig.encode()).hex(): v for sig, v in SIGNATURES.items()}
ONE = '0x' + keccak256(b'RemoveLiquidityOne(address,uint256,uint256,uint256)').hex()
ONE_INDEXED = '0x' + keccak256(b'RemoveLiquidityOne(address,int128,uint256,uint256,uint256)').hex()


@cached(source_id="spark-reporting.sde-block-day.v1")
def event_day(block):
    from .cash_flow.collector import rpc
    header = rpc('ethereum', 'eth_getBlockByNumber', [hex(block), False])
    if int(header['number'], 16) != block:
        raise ValueError('SDE event block differs')
    return datetime.fromtimestamp(int(header['timestamp'], 16), UTC).date().isoformat()


def curve_receipt(receipt, holder, *, expected_tx, expected_block):
    if (not receipt or receipt.get('transactionHash', '').lower() != expected_tx.lower()
            or int(receipt.get('status', '0x0'), 16) != 1
            or int(receipt['blockNumber'], 16) != expected_block):
        raise ValueError('Curve liquidity receipt identity differs')
    principal = 0
    witnessed = []
    seen = set()
    for row in receipt['logs']:
        if row.get('removed') or row.get('transactionHash', '').lower() != expected_tx.lower() or row.get('blockNumber') != receipt['blockNumber']:
            raise ValueError('Curve receipt log identity differs')
        index = int(row['logIndex'], 16)
        if index in seen:
            raise ValueError('Duplicate Curve receipt log')
        seen.add(index)
        topics = [x.lower() for x in row.get('topics', [])]
        if row['address'].lower() != CURVE or len(topics) != 2 or topics[1] != topic(holder):
            continue
        words = [int(row['data'][i:i + 64], 16) for i in range(2, len(row['data']), 64)]
        if topics[0] in DYNAMIC:
            if not words or words[0] % 32:
                raise ValueError('Invalid Curve amount array offset')
            offset = words[0] // 32
            if offset < 3 or offset + 2 >= len(words) or words[offset] != 2:
                raise ValueError('Invalid Curve amount array')
            amount = DYNAMIC[topics[0]] * words[offset + 2]
        elif topics[0] in TOPICS:
            sign, offset = TOPICS[topics[0]]
            if len(words) < 5:
                raise ValueError('Malformed Curve liquidity event')
            amount = sign * words[offset]
        elif topics[0] == ONE_INDEXED:
            if len(words) != 4:
                raise ValueError('Malformed indexed Curve withdrawal')
            amount = -words[2] if words[0] == 1 else 0
        elif topics[0] == ONE:
            if len(words) != 3:
                raise ValueError('Malformed Curve withdrawal')
            # This ABI omits the coin index. Require the exact USDT cash leg;
            # another coin withdrawal must never become USDT principal.
            transfers = [r for r in receipt['logs'] if r['address'].lower() == USDT
                         and len(r.get('topics', [])) == 3 and r['topics'][0].lower() == TRANSFER_TOPIC0
                         and r['topics'][1].lower() == topic(CURVE) and r['topics'][2].lower() == topic(holder)
                         and int(r['data'], 16) == words[1]]
            if len(transfers) > 1:
                raise ValueError('Ambiguous Curve one-coin cash leg')
            amount = -words[1] if transfers else 0
        else:
            continue
        principal += amount
        witnessed.append(index)
    return principal, witnessed


def collect(source, retained):
    holder = source.prime.alm[next(c for c in source.prime.alm if str(c) == 'ethereum')].hex
    result = {'status': 'available', 'entries': [], 'errors': {}, 'basis': 'authenticated_liquidity_principal_only_excludes_swaps'}
    by_tx = defaultdict(lambda: {'principal_raw': 0, 'event_indices': []})
    v4 = retained.get('amm_fees', {}).get('S62', {})
    if v4.get('status') == 'available' and v4.get('capital_reconciliation', {}).get('status') == 'available':
        currencies = [a.lower() for a in v4.get('currencies', [])]
        if USDT not in currencies:
            raise ValueError('V4 designated cash currency differs')
        coin = currencies.index(USDT)
        for e in v4.get('capital_events', []):
            # Date is verified from the event's actual block, not month start.
            day = event_day(e['block'])
            if not source.period.start.isoformat() <= day <= source.period.end.isoformat():
                raise ValueError('V4 event day is outside the source period')
            if day < '2026-05-26':
                continue
            if len(e.get('principal_raw', [])) != len(currencies):
                raise ValueError('V4 principal receipt proof is missing')
            group = by_tx[e['transaction']]
            group['principal_raw'] += int(e['principal_raw'][coin])
            group['event_indices'].append(e['log_index'])
        for tx, data in by_tx.items():
            result['entries'].append(dict(chain='ethereum', transaction=tx, counterparty=MANAGER,
                principal_raw=str(data['principal_raw']), token=USDT, venue='S62',
                event_indices=data['event_indices'], receipt_bridge_digest=v4['capital_reconciliation']['receipt_digest']))
    elif source.period.end.isoformat() >= '2026-05-26':
        result['errors']['S62'] = 'Verified V4 principal/receipt source unavailable'
    if source.period.start.isoformat() <= '2026-06-25':
        rows = source.logs('ethereum', [{'address': [CURVE], 'topics': [[TRANSFER_TOPIC0], [topic(holder)]]},
                                         {'address': [CURVE], 'topics': [[TRANSFER_TOPIC0], [], [topic(holder)]]}])
        selected = {}
        for row in rows:
            day = datetime.fromtimestamp(row.block_time, UTC).date().isoformat()
            if day <= '2026-06-25':
                prior = selected.setdefault(row.transaction_hash, {'block': row.block_number, 'mint_or_burn': False})
                prior['mint_or_burn'] |= row.topic1 == ZERO or row.topic2 == ZERO
        try:
            # Enforce the reviewed coin order for the event amount array.
            if source.call('ethereum', CURVE, 'coins(uint256)', source.closing_pins['ethereum'], 1)[-40:].lower() != USDT[2:]:
                raise ValueError('Curve USDT coin order differs')
            for tx, details in selected.items():
                block = details['block']
                receipt = capital_receipt(tx)
                amount, indices = curve_receipt(receipt, holder, expected_tx=tx, expected_block=block)
                if details['mint_or_burn'] and not indices:
                    raise ValueError('Unrecognized Curve liquidity event')
                result['entries'].append(dict(chain='ethereum', transaction=tx, counterparty=CURVE,
                    principal_raw=str(amount), token=USDT, venue='S24', event_indices=indices,
                    receipt_digest=digest(receipt)))
        except Exception as exc:
            result['errors']['S24'] = type(exc).__name__
    if result['errors']: result['status'] = 'partial'
    result['event_digest'] = digest(result['entries'])
    return result


def validate_document(document):
    """Fail closed on malformed/duplicate principal witnesses before publication."""
    import re
    cash = document.get('sde_cash')
    if cash is None:
        return
    if (cash.get('status') not in ('available', 'partial')
            or cash.get('event_digest') != digest(cash.get('entries'))
            or (cash['status'] == 'available' and cash.get('errors'))):
        raise ValueError('Invalid SDE principal source identity')
    seen = set()
    for entry in cash['entries']:
        venue = entry.get('venue')
        expected = {'S24': CURVE, 'S62': MANAGER}.get(venue)
        identity = (entry.get('chain'), entry.get('transaction'), venue)
        proof = entry.get('receipt_digest') if venue == 'S24' else entry.get('receipt_bridge_digest')
        if (identity in seen or entry.get('chain') != 'ethereum' or not expected
                or entry.get('counterparty') != expected or entry.get('token') != USDT
                or not re.fullmatch('0x[a-f0-9]{64}', entry.get('transaction', ''))
                or not re.fullmatch(r'-?\d+', entry.get('principal_raw', ''))
                or not re.fullmatch('[a-f0-9]{64}', proof or '')
                or not isinstance(entry.get('event_indices'), list)
                or any(type(n) is not int or n < 0 for n in entry['event_indices'])
                or len(set(entry['event_indices'])) != len(entry['event_indices'])):
            raise ValueError('Malformed or duplicate SDE principal witness')
        seen.add(identity)
