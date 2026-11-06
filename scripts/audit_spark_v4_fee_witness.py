"""Read-only proof of Spark S61 fees from independent position checkpoints.

StateLibrary.getPositionInfo and Position.update at Uniswap/v4-core
46c6834698c48bc4a463a86d8420f4eb1d7f3b75 define the storage key and integer fee
calculation. Restrict this witness to one modification of the position in its
block, a hookless pool, and actual cash delivered to the Spark ALM. No inferred
residual is used to calculate fees, and no replay or revenue is modified.
"""
import argparse
import gzip
import hashlib
import json
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.extract.uniswap_v4 import (
    SEL_GET_POOL_AND_POSITION_INFO,
    SEL_GET_POSITION_LIQUIDITY,
    TOPIC_MODIFY_LIQUIDITY,
)

POOL_MANAGER = '0x000000000004444c5dc75cb358380d2e3de08a90'
MANAGER = '0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e'
HOLDER = '0x1601843c5e9bc251a3272907010afa41fa18347e'
COINS = {'0x6c3ea9036406852006290770bedfcaba0e23a0e8': 6,
         '0xdc035d45d973e3ec169d2276ddab16f1e407384f': 18}
MOD = 1 << 256


def audit(proof):
    receipt, batch = proof['item']['receipt'], proof['item']['batch']
    block, tid = proof['block'], proof['position_id']
    tx = receipt['transactionHash']
    if (proof['pool_manager'] != POOL_MANAGER or proof['position_manager'] != MANAGER
            or int(receipt['status'], 16) != 1 or int(receipt['blockNumber'], 16) != block
            or batch['identity'] != 'ethereum:'+tx or batch['block'] != block):
        raise ValueError('Unexpected V4 transaction')
    events = [r for r in proof['block_modify_events'] if int(r['data'][-64:], 16) == tid]
    if len(events) != 1:
        raise ValueError('Multiple or missing position modifications in block')
    event = events[0]
    if (event not in receipt['logs'] or event['address'] != POOL_MANAGER
            or event['topics'][0] != TOPIC_MODIFY_LIQUIDITY
            or event['topics'][2] != '0x'+MANAGER[2:].rjust(64, '0')
            or event['transactionHash'] != tx or int(event['blockNumber'], 16) != block
            or event.get('removed') or len(event['data']) != 258):
        raise ValueError('Invalid position modification witness')
    if any(int(r['blockNumber'], 16) != block or r['address'] != POOL_MANAGER
           or r['topics'] != event['topics'] or r.get('removed')
           for r in proof['block_modify_events']):
        raise ValueError('Block modification query has inconsistent coordinates')
    words = [int(event['data'][2+i*64:2+(i+1)*64], 16) for i in range(4)]
    lower, upper = (words[i] % (1 << 24) for i in (0, 1))
    position_key = keccak256(bytes.fromhex(MANAGER[2:])+lower.to_bytes(3, 'big')
                            +upper.to_bytes(3, 'big')+tid.to_bytes(32, 'big'))
    pool_id = bytes.fromhex(event['topics'][1][2:])
    state_slot = int.from_bytes(keccak256(pool_id+(6).to_bytes(32, 'big')), 'big')
    slot = int.from_bytes(keccak256(position_key+((state_slot+6) % MOD).to_bytes(32, 'big')), 'big')
    reads = {(r['block'], r['key']): r for r in proof['reads']}
    if len(reads) != len(proof['reads']):
        raise ValueError('Duplicate position state read')

    def read(at, key, to, data):
        r = reads[at, key]
        if r['to'] != to or r['data'] != data:
            raise ValueError('Wrong position storage read')
        return r['result'].removeprefix('0x')

    states = {}
    for at in (block-1, block):
        states[at] = [int(read(at, i, POOL_MANAGER, '0x1e2eaeaf'+f'{(slot+i) % MOD:064x}'), 16)
                      for i in range(3)]
        liquidity = int(read(at, 'liquidity', MANAGER, SEL_GET_POSITION_LIQUIDITY+f'{tid:064x}'), 16)
        if states[at][0] != liquidity or liquidity >= 1 << 128:
            raise ValueError('Liquidity checkpoint mismatch')
    before, after = states[block-1], states[block]
    delta = words[2] if words[2] < 1 << 255 else words[2]-MOD
    if before[0]+delta != after[0] or delta >= 0:
        raise ValueError('Liquidity withdrawal does not match checkpoints')
    info = read(block, 'info', MANAGER, SEL_GET_POOL_AND_POSITION_INFO+f'{tid:064x}')
    owner = '0x'+read(block, 'owner', MANAGER, '0x6352211e'+f'{tid:064x}')[-40:]
    if (len(info) != 384 or keccak256(bytes.fromhex(info[:320])) != pool_id
            or int(info[256:320], 16) or owner != HOLDER
            or (int(info[320:], 16) >> 8) % (1 << 24) != lower
            or (int(info[320:], 16) >> 32) % (1 << 24) != upper):
        raise ValueError('Pool key, ticks, hooks or owner mismatch')
    coins = ['0x'+info[i*64+24:(i+1)*64] for i in range(2)]
    if set(coins) != set(COINS):
        raise ValueError('Unsupported V4 fee token')
    # Position.update uses previous liquidity and unchecked uint256 growth
    # subtraction, followed by FullMath.mulDiv(..., Q128).
    fees = [((after[i]-before[i]) % MOD)*before[0]//(1 << 128) for i in (1, 2)]
    cash = dict.fromkeys(coins, 0)
    indexes = set()
    for r in receipt['logs']:
        if (r['logIndex'] in indexes or r.get('removed') or r['transactionHash'] != tx
                or int(r['blockNumber'], 16) != block):
            raise ValueError('Invalid receipt log coordinates')
        indexes.add(r['logIndex'])
        topics = r['topics']
        if (r['address'] in cash and len(topics) == 3 and topics[0] == TRANSFER_TOPIC0
                and topics[1][-40:] == POOL_MANAGER[2:] and topics[2][-40:] == HOLDER[2:]):
            cash[r['address']] += int(r['data'], 16)
    with localcontext() as ctx:
        ctx.prec = 60
        fee_usd, principal_usd = D(0), D(0)
        for coin, fee in zip(coins, fees, strict=True):
            if cash[coin] < fee:
                raise ValueError('Fees exceed delivered cash')
            fee_usd += D(fee)/10**COINS[coin]
            principal_usd += D(cash[coin]-fee)/10**COINS[coin]
        account = f'nft:ethereum:{MANAGER}:{tid}:S61'
        nft = [m for m in batch['movements'] if m['account'] == account]
        if len(nft) != 1 or abs(D(nft[0]['change'])+principal_usd) > D('1e-12'):
            raise ValueError('Independent principal differs from normalized NFT movement')
        return {'transaction': tx, 'block': block, 'venue': 'S61', 'position_id': tid,
                'fee_raw_by_token': dict(zip(coins, fees, strict=True)),
                'fees_at_par': str(fee_usd), 'returned_principal_at_par': str(principal_usd),
                'historical_unmatched_receipt': proof['item']['residual']['amount'],
                'remaining_signed_receipt': str(D(proof['item']['residual']['amount'])-fee_usd),
                'scope': 'Independent fee witness only; replay classification and published revenue unchanged.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == args.evidence.resolve():
        raise ValueError('Cannot overwrite evidence')
    raw = args.evidence.read_bytes()
    result = audit(json.loads(gzip.decompress(raw)))
    result['evidence_sha256'] = hashlib.sha256(raw).hexdigest()
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
