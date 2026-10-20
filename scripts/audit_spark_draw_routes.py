"""Receipt-authenticated USDS draw routes for the funding-origin diagnostic only.

A whole transaction may mix Sky and Savings funding while its USDS leg is
isolated. Keep every movement and all debt; separate only a witnessed Buffer ->
ALM -> sUSDS route. Existing USDS cash is not promoted to new Sky borrowing.
"""
from dataclasses import replace
from decimal import Decimal as D
from decimal import localcontext

from settle.compute.spark_separate_savings_routes import ILK, SUSDS, USDS
from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0

ALM = '0x1601843c5e9bc251a3272907010afa41fa18347e'
BUFFER = '0xc395d150e71378b47a1b8e9de0c1a83b75a08324'
DEPOSIT = '0x' + keccak256(b'Deposit(address,address,uint256,uint256)').hex()


def validate(batch, receipt):
    with localcontext() as ctx:
        ctx.prec = 60
        return _validate(batch, receipt)


def _validate(b, receipt):
    tx = b.identity.split(':')[1]
    if (b.identity != 'ethereum:' + tx or b.chain != 'ethereum'
            or receipt['transactionHash'].lower() != tx or int(receipt['status'], 16) != 1
            or int(receipt['blockNumber'], 16) != b.block
            or set(b.minted_by_ilk) != {ILK} or b.minted_by_ilk[ILK] != b.minted or b.minted <= 0):
        raise ValueError('Draw receipt metadata or debt mismatch')
    logs = receipt['logs']
    if len({int(log['logIndex'], 16) for log in logs}) != len(logs):
        raise ValueError('Duplicate receipt log')
    if any(log.get('removed') or log['transactionHash'].lower() != tx
           or int(log['blockNumber'], 16) != b.block for log in logs):
        raise ValueError('Receipt contains inconsistent logs')
    transfers = [log for log in logs if log['address'].lower() == USDS.split(':')[-1]
                 and log['topics'][0] == TRANSFER_TOPIC0]
    ingress = [log for log in transfers if log['topics'][2][-40:] == ALM[2:]]
    egress = [log for log in transfers if log['topics'][1][-40:] == ALM[2:]]
    if (len(ingress) != 1 or len(egress) != 1
            or ingress[0]['topics'][1][-40:] != BUFFER[2:]
            or egress[0]['topics'][2][-40:] != SUSDS.split(':')[-1][2:]
            or int(ingress[0]['logIndex'], 16) >= int(egress[0]['logIndex'], 16)):
        raise ValueError('USDS route is not isolated Buffer-to-sUSDS')
    drawn = D(int(ingress[0]['data'], 16)) / 10**18
    paid = D(int(egress[0]['data'], 16)) / 10**18
    deposits = [log for log in logs if log['address'].lower() == SUSDS.split(':')[-1]
                and log['topics'][0] == DEPOSIT and log['topics'][2][-40:] == ALM[2:]]
    if (len(deposits) != 1 or deposits[0]['topics'][1][-40:] != ALM[2:]
            or D(int(deposits[0]['data'][2:66], 16)) / 10**18 != paid):
        raise ValueError('sUSDS deposit differs from USDS payment')
    shares = int(deposits[0]['data'][66:], 16)
    share_transfers = [log for log in logs if log['address'].lower() == SUSDS.split(':')[-1]
                      and log['topics'][0] == TRANSFER_TOPIC0
                      and any(t[-40:] == ALM[2:] for t in log['topics'][1:])]
    mints = [log for log in share_transfers if int(log['topics'][1], 16) == 0
             and log['topics'][2][-40:] == ALM[2:]]
    outgoing = [log for log in share_transfers if log['topics'][1][-40:] == ALM[2:]
                and log['topics'][2][-40:] != ALM[2:]]
    forwarded = sum(int(log['data'], 16) for log in outgoing)
    if (len(mints) != 1 or int(mints[0]['data'], 16) != shares
            or len(mints) + len(outgoing) != len(share_transfers)
            or not 0 <= forwarded < shares
            or any(int(log['logIndex'], 16) <= int(mints[0]['logIndex'], 16) for log in outgoing)):
        raise ValueError('sUSDS shares do not match isolated mint')
    ms = {m.account: m for m in b.movements}
    if len(ms) != len(b.movements) or not {USDS, SUSDS} <= ms.keys():
        raise ValueError('Missing or duplicated USDS position movement')
    cash, position = ms[USDS], ms[SUSDS]
    if (abs(drawn - b.minted) > D('1e-18') or cash.external_income or cash.preserve_basis
            or cash.value_before < 0 or cash.value_before + cash.change != 0
            or cash.change != drawn - paid or position.external_income or position.preserve_basis
            or not D(0) < position.change <= paid
            or abs(position.change - paid * (shares - forwarded) / shares) > D('1e-8')):
        raise ValueError('Normalized USDS route differs from receipt')
    return {'transaction': b.identity, 'block': b.block, 'drawn_usds': str(drawn),
            'deposited_usds': str(paid), 'existing_cash_spent': str(-cash.change),
            'normalized_position_added': str(position.change),
            # Charge all forwarded shares against this draw first. Their later
            # allocation remains unclassified; no borrowed basis is counted twice.
            'forwarded_susds_shares_raw': str(forwarded),
            'payment_minus_normalized_value': str(paid - position.change)}


def separate(history, receipts):
    """Reject absent proofs and apply each supplied receipt exactly once."""
    by_id = {b.identity: b for b in history.batches}
    proofs = []
    replacements = {}
    for tx, receipt in receipts.items():
        identity = 'ethereum:' + tx
        if identity not in by_id:
            raise ValueError('Receipt has no original normalized transaction')
        b = by_id[identity]
        proofs.append(validate(b, receipt))
        sky = replace(b, identity=identity + ':audited-sky-usds-route',
                      movements=tuple(m for m in b.movements if m.account in {USDS, SUSDS}),
                      external_funding=(), funding_assumption=None)
        other = replace(b, identity=identity + ':audited-other-routes', minted=D(0), minted_by_ilk={},
                        log_index=b.log_index + 1,
                        movements=tuple(m for m in b.movements if m.account not in {USDS, SUSDS}))
        replacements[identity] = (sky, other)
    return replace(history, batches=tuple(x for b in history.batches
                                         for x in replacements.get(b.identity, (b,)))), proofs
