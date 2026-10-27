"""Authenticate Spark's gross USDS draw -> sUSDS route despite later repayments.

Savings assets and Sky-minted USDS are separate funding legs. This diagnostic
labels only receipt-proven position contributions; it does not change debt,
capital balances, or the provisional accounting for Savings repayments.
"""
from collections import defaultdict
from decimal import Decimal as D
from decimal import localcontext

from audit_spark_draw_routes import ALM, BUFFER, DEPOSIT

from settle.compute.spark_separate_savings_routes import ILK, SUSDS, USDS
from settle.domain.config import load_prime_by_id
from settle.domain.primes import PricingCategory
from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.sources.hypersync_debt import _FROB_T0, _VAT, _decode_dart

VAULT = '0x691a6c29e9e96dd897718305427ad5d534db16ba'
DRAW = '0x' + keccak256(b'Draw(address,uint256)').hex()
WIPE = '0x' + keccak256(b'Wipe(address,uint256)').hex()
TAKE = '0x' + keccak256(b'Take(address,uint256)').hex()
WAD = D(10)**18
EPS = D('1e-8')


def address(topic):
    return '0x' + topic[-40:].lower()


def index(log):
    return int(log['logIndex'], 16)


def savings_receipts(b, logs):
    specs = {v.token.address.hex: v for v in load_prime_by_id('spark').venues
             if v.chain.value == 'ethereum' and v.pricing_category == PricingCategory.SPARK_SAVINGS_V2
             and v.underlying is not None}
    expected = defaultdict(D)
    for op in b.external_funding:
        if op.kind == 'draw':
            expected[op.source] += op.amount
    actual = defaultdict(D)
    for source, venue in specs.items():
        takes = [log for log in logs if log['address'] == source and log['topics'][0] == TAKE
                 and address(log['topics'][1]) == ALM]
        transfers = [log for log in logs if log['address'] == venue.underlying.address.hex
                     and log['topics'][0] == TRANSFER_TOPIC0
                     and address(log['topics'][1]) == source and address(log['topics'][2]) == ALM]
        taken = sum(int(log['data'], 16) for log in takes)
        paid = sum(int(log['data'], 16) for log in transfers)
        if taken != paid:
            raise ValueError('Savings Take differs from actual token receipt')
        if taken:
            actual['ethereum:' + source] = D(taken) / 10**venue.underlying.decimals
    if dict(actual) != {k: v for k, v in expected.items() if v}:
        raise ValueError('Savings receipts differ from normalized external draws')
    return {k: str(v) for k, v in actual.items()}


def validate(batch, receipt, *, allow_mixed_prefix=False):
    with localcontext() as ctx:
        ctx.prec = 60
        return _validate(batch, receipt, allow_mixed_prefix)


def _validate(b, receipt, allow_mixed_prefix):
    tx = receipt['transactionHash'].lower()
    if (b.identity != 'ethereum:' + tx or b.chain != 'ethereum'
            or int(receipt['status'], 16) != 1 or int(receipt['blockNumber'], 16) != b.block
            or set(b.minted_by_ilk) != {ILK} or b.minted_by_ilk[ILK] != b.minted):
        raise ValueError('Ordered route receipt/debt metadata mismatch')
    logs = sorted(receipt['logs'], key=index)
    if len({index(log) for log in logs}) != len(logs) or any(
            log.get('removed') or log['transactionHash'].lower() != tx
            or int(log['blockNumber'], 16) != b.block for log in logs):
        raise ValueError('Inconsistent or duplicated ordered route logs')
    vault_events = [log for log in logs if log['address'] == VAULT and log['topics'][0] in (DRAW, WIPE)]
    draws = [log for log in vault_events if log['topics'][0] == DRAW]
    wipes = [log for log in vault_events if log['topics'][0] == WIPE]
    if len(draws) != 1 or any(address(log['topics'][1]) != ALM for log in vault_events):
        raise ValueError('Requires one authenticated AllocatorVault draw')
    drawn = D(int(draws[0]['data'], 16)) / WAD
    wiped = sum((D(int(log['data'], 16)) / WAD for log in wipes), D(0))
    # Compare token-denominated events to already rate-normalized Vat debt.
    # Allow sub-picodollar context/rounding noise; never assume Vat.rate == 1.
    if drawn <= 0 or abs(drawn - wiped - b.minted) > D('1e-12'):
        raise ValueError('Gross Draw/Wipe do not reconcile to normalized net debt')
    frobs = [log for log in logs if log['address'] == _VAT and log['topics'][0] == _FROB_T0
             and log['topics'][1] == ILK]
    if any(address(log['topics'][2]) != VAULT for log in frobs):
        raise ValueError('Unexpected urn for Spark ilk debt')
    positive = [log for log in frobs if _decode_dart(log['data']) > 0]
    negative = [log for log in frobs if _decode_dart(log['data']) < 0]
    if (len(positive) != 1 or len(negative) != len(wipes) or len(frobs) != len(vault_events)
            or index(positive[0]) >= index(draws[0])
            or any(index(frob) >= index(wipe) for frob, wipe in zip(negative, wipes, strict=True))):
        raise ValueError('Vault events lack corresponding signed Vat frobs')
    transfers = [log for log in logs if log['address'] == USDS.split(':')[-1]
                 and log['topics'][0] == TRANSFER_TOPIC0]
    mints = [log for log in transfers if int(log['topics'][1], 16) == 0
             and address(log['topics'][2]) == BUFFER]
    ingress = [log for log in transfers if address(log['topics'][1]) == BUFFER
               and address(log['topics'][2]) == ALM]
    deposits = [log for log in logs if log['address'] == SUSDS.split(':')[-1]
                and log['topics'][0] == DEPOSIT and address(log['topics'][2]) == ALM]
    payments = [log for log in transfers if address(log['topics'][1]) == ALM
                and address(log['topics'][2]) == SUSDS.split(':')[-1]]
    if (len(mints) != 1 or len(ingress) != 1 or len(deposits) != 1 or len(payments) != 1
            or D(int(mints[0]['data'], 16)) / WAD != drawn
            or D(int(ingress[0]['data'], 16)) / WAD != drawn
            or not index(positive[0]) < index(mints[0]) < index(draws[0]) < index(ingress[0]) < index(payments[0])
            or address(deposits[0]['topics'][1]) != ALM
            or int(deposits[0]['data'][2:66], 16) != int(payments[0]['data'], 16)
            or index(deposits[0]) <= index(payments[0])):
        raise ValueError('No isolated minted-USDS prefix into sUSDS')
    if any(index(log) <= index(deposits[0]) for log in wipes + negative):
        raise ValueError('Debt repayment precedes completion of deposit')
    cash_events = [log for log in transfers if ALM in (address(log['topics'][1]), address(log['topics'][2]))]
    prefix = [log for log in cash_events if index(log) <= index(payments[0])]
    extra = [log for log in prefix if log not in (ingress[0], payments[0])]
    if prefix != [ingress[0], payments[0]] and not (
            allow_mixed_prefix and prefix[-1] == payments[0]
            and all(address(log['topics'][2]) == ALM
                    and address(log['topics'][1]) != ALM for log in extra)):
        raise ValueError('Other USDS cash contaminates deposit prefix')
    # All additional cash is unclassified, even if it comes from a familiar
    # venue. Charge it against the deposit before attributing any Sky funding.
    other_cash = sum((D(int(log['data'], 16)) / WAD for log in extra), D(0))
    ms = {m.account: m for m in b.movements}
    if len(ms) != len(b.movements) or not {USDS, SUSDS} <= ms.keys():
        raise ValueError('Missing or duplicate position/cash movement')
    cash, position = ms[USDS], ms[SUSDS]
    if cash.external_income or position.external_income or cash.preserve_basis or position.preserve_basis:
        raise ValueError('Income or special basis needs separate treatment')
    balance = cash.value_before
    if balance < 0:
        raise ValueError('Negative opening cash')
    for log in cash_events:
        amount = D(int(log['data'], 16)) / WAD
        balance += amount if address(log['topics'][2]) == ALM else -amount
        if balance < 0:
            raise ValueError('Ordered USDS cash balance becomes negative')
    if balance != cash.value_before + cash.change:
        raise ValueError('Ordered cash transfers differ from normalized cash')
    paid = D(int(payments[0]['data'], 16)) / WAD
    shares = int(deposits[0]['data'][66:], 16)
    share_events = [log for log in logs if log['address'] == SUSDS.split(':')[-1]
                    and log['topics'][0] == TRANSFER_TOPIC0
                    and ALM in (address(log['topics'][1]), address(log['topics'][2]))]
    share_mints = [log for log in share_events if int(log['topics'][1], 16) == 0
                   and address(log['topics'][2]) == ALM]
    outgoing = [log for log in share_events if address(log['topics'][1]) == ALM
                and address(log['topics'][2]) != ALM]
    forwarded = sum(int(log['data'], 16) for log in outgoing)
    if (len(share_mints) != 1 or int(share_mints[0]['data'], 16) != shares
            or len(share_events) != len(outgoing) + 1 or not 0 <= forwarded < shares
            or any(index(log) <= index(deposits[0]) for log in outgoing)
            or position.change <= 0 or abs(position.change - paid * (shares - forwarded) / shares) > EPS):
        raise ValueError('Net sUSDS position differs from deposited/forwarded shares')
    # Spend all unclassified opening cash first and remove all forwarded value
    # from new Sky funding first. Neither can inflate the proven contribution.
    known = max(D(0), min(drawn, paid - cash.value_before - other_cash) - paid * forwarded / shares)
    known = min(known, position.change)
    proof = {'transaction': b.identity, 'block': b.block, 'gross_draw_usds': str(drawn),
            'gross_wipe_usds': str(wiped), 'normalized_net_debt': str(b.minted),
            'savings_cash_received': savings_receipts(b, logs),
            'usds_deposited': str(paid), 'opening_unclassified_usds': str(cash.value_before),
            'forwarded_susds_shares_raw': str(forwarded),
            'verified_borrowed_contribution': str(known)}
    if allow_mixed_prefix:
        proof['additional_unclassified_usds'] = str(other_cash)
    return proof
