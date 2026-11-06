"""Authenticate the October 8, 2025 aUSDS reward receipt, without editing a replay.

The executed October 2 payload explicitly claims Aave Core aUSDS rewards:
https://github.com/sparkdotfi/spark-spells/blob/dc2a653f4b2f5491641276e913cae06e221ce8ea/archive/20251002/SparkEthereum_20251002.sol#L180
This explains funding purpose; it does not change published revenue or certify
the running replay's remaining provenance.
"""
import argparse
import gzip
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path

from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0

TX = '0x0af39af528cd328028432e17f451aff046b19536824007f2e1665f1b7ed5b2e3'
HOLDER = '0x1601843c5e9bc251a3272907010afa41fa18347e'
TOKEN = '0x32a6268f9ba3642dda7892add74f1d34469a4259'
CONTROLLER = '0x8164cc65827dcfe994ab23944cbc90e0aa80bfcb'
PAYER = '0xac140648435d03f784879cd789130f22ef588fcd'
CLAIM = '0x'+keccak256(b'RewardsClaimed(address,address,address,address,uint256)').hex()


def audit(proof):
    receipt, batch = proof['receipt'], proof['batch']
    if (receipt['transactionHash'] != TX or int(receipt['status'], 16) != 1
            or batch['identity'] != 'ethereum:'+TX or batch['day'] != '2025-10-08'
            or batch['block'] != int(receipt['blockNumber'], 16)
            or D(batch['minted']) or batch['minted_by_ilk'] or batch['external_funding']):
        raise ValueError('Unexpected reward transaction or funding')
    claims, transfers, indexes = [], [], set()
    for row in receipt['logs']:
        if (row['transactionHash'] != TX or row['blockNumber'] != receipt['blockNumber']
                or row['logIndex'] in indexes or row.get('removed', False)):
            raise ValueError('Invalid reward receipt log identity')
        indexes.add(row['logIndex'])
        topics = row['topics']
        if (row['address'] == CONTROLLER and len(topics) > 2
                and topics[0] == CLAIM and topics[2].endswith(TOKEN[2:])):
            raw = row['data'].removeprefix('0x')
            if (len(topics) != 4 or len(raw) != 128
                    or '0x'+topics[1][-40:] != HOLDER or '0x'+topics[3][-40:] != HOLDER
                    or '0x'+raw[:64][-40:] != HOLDER):
                raise ValueError('Reward beneficiary or claimer differs from ALM')
            claims.append((int(raw[64:], 16), row['logIndex']))
        if (row['address'] == TOKEN and topics[0] == TRANSFER_TOPIC0
                and len(topics) == 3 and '0x'+topics[1][-40:] == PAYER
                and '0x'+topics[2][-40:] == HOLDER):
            if len(row['data']) != 66:
                raise ValueError('Invalid reward transfer')
            transfers.append((int(row['data'], 16), row['logIndex']))
    if len(claims) != 1 or len(transfers) != 1 or claims[0][0] != transfers[0][0] or claims[0][0] <= 0:
        raise ValueError('Claim does not match delivered reward tokens')
    amount = D(claims[0][0])/10**18
    movements = [r for r in batch['movements'] if r['account'] == f'ethereum:{HOLDER}:{TOKEN}']
    if (len(movements) != 1 or abs(D(movements[0]['change'])-amount) > D('1e-18')
            or D(movements[0]['external_income'])
            or any(D(r['change']) != D(r['external_income']) for r in batch['movements'] if r not in movements)):
        raise ValueError('Reward does not reproduce the unexplained normalized receipt')
    return {'transaction': TX, 'block': batch['block'], 'day': batch['day'],
            'token': TOKEN, 'amount': str(amount), 'purpose': 'Aave Core aUSDS rewards',
            'claim_log_index': claims[0][1], 'transfer_log_index': transfers[0][1],
            'scope': 'Confirmed economic explanation only; running replay classification unchanged.'}


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
