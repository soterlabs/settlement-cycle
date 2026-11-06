"""Carry proven spUSDS borrowing through isolated USDS withdrawal/deposit routes.

A known immediate payer alone is insufficient: source-account history supplies
released borrowed basis, while the receipt proves an isolated cash route.
"""
from decimal import Decimal as D
from decimal import localcontext

from audit_scoped_borrowing_origins import trace
from audit_spark_ordered_draws import ALM, DEPOSIT, DRAW, SUSDS, USDS, VAULT, WAD, address, index

from settle.extract.transfer_logs import TRANSFER_TOPIC0

SPUSDS = USDS.rsplit(':',1)[0] + ':0xc02ab1a5eaa8d1b114ef786d9bde108cd4364359'


def validate(batch, receipt):
    with localcontext() as ctx:
        ctx.prec = 60
        return _validate(batch, receipt)


def _validate(b, receipt):
    if (b.identity != 'ethereum:'+receipt['transactionHash'].lower() or b.chain != 'ethereum'
            or int(receipt['status'],16)!=1 or int(receipt['blockNumber'],16)!=b.block
            or b.minted or any(b.minted_by_ilk.values()) or b.external_funding or b.funding_assumption):
        raise ValueError('Not an isolated zero-debt spUSDS reallocation')
    ms={m.account:m for m in b.movements}
    if len(ms)!=len(b.movements) or set(ms)!={SPUSDS,USDS,SUSDS}:
        raise ValueError('Other allocation movements require separate routing')
    if any(m.external_income or m.preserve_basis for m in ms.values()):
        raise ValueError('Special basis or income requires separate routing')
    cash,source,target=ms[USDS],ms[SPUSDS],ms[SUSDS]
    if cash.value_before or cash.change or source.change>=0 or target.change<=0:
        raise ValueError('Non-isolated reallocation cash or position')
    logs=sorted(receipt['logs'],key=index)
    if len({index(log) for log in logs})!=len(logs) or any(
            log.get('removed') or log['transactionHash'].lower()!=receipt['transactionHash'].lower()
            or int(log['blockNumber'],16)!=b.block for log in logs):
        raise ValueError('Inconsistent reallocation receipt logs')
    if any(log['address']==VAULT and log['topics'][0]==DRAW for log in logs):
        raise ValueError('Gross draw is not a pure reallocation')
    cash_logs=[log for log in logs if log['address']==USDS.split(':')[-1]
               and log['topics'][0]==TRANSFER_TOPIC0
               and ALM in (address(log['topics'][1]),address(log['topics'][2]))]
    if len(cash_logs)!=2:
        raise ValueError('Reallocation requires sole USDS ingress and egress')
    incoming,outgoing=cash_logs
    if (address(incoming['topics'][1])!=SPUSDS.split(':')[-1]
            or address(incoming['topics'][2])!=ALM or address(outgoing['topics'][1])!=ALM
            or address(outgoing['topics'][2])!=SUSDS.split(':')[-1]
            or int(incoming['data'],16)!=int(outgoing['data'],16)):
        raise ValueError('spUSDS cash does not match sUSDS payment')
    paid=D(int(incoming['data'],16))/WAD
    if abs(paid+source.change)>D('1e-8') or abs(paid-target.change)>D('1e-8'):
        raise ValueError('Cash differs from normalized position movements')
    deposits=[log for log in logs if log['address']==SUSDS.split(':')[-1] and log['topics'][0]==DEPOSIT]
    shares=[log for log in logs if log['address']==SUSDS.split(':')[-1] and log['topics'][0]==TRANSFER_TOPIC0
            and ALM in (address(log['topics'][1]),address(log['topics'][2]))]
    if (len(deposits)!=1 or len(shares)!=1 or address(deposits[0]['topics'][1])!=ALM
            or address(deposits[0]['topics'][2])!=ALM
            or int(deposits[0]['data'][2:66],16)!=int(outgoing['data'],16)
            or int(shares[0]['topics'][1],16)!=0 or address(shares[0]['topics'][2])!=ALM
            or int(shares[0]['data'],16)!=int(deposits[0]['data'][66:],16)
            or not index(incoming)<index(outgoing)<min(index(shares[0]),index(deposits[0]))):
        raise ValueError('Reallocation deposit/share evidence mismatch')
    return paid


def contributions(history, receipts, end):
    if history.venue_accounts.get('S1')!=SPUSDS or history.custody_accounts.get('S1'):
        raise ValueError('Unexpected source allocation identity')
    source=trace(history,('S1',),end,bound_external_funding=True)
    by_id={b.identity:b for b in history.batches}
    proofs=[]
    for tx,receipt in sorted(receipts.items()):
        b=by_id.get('ethereum:'+tx)
        if b is None or b.day>end:
            continue
        try:
            paid=validate(b,receipt)
        except ValueError:
            continue
        borrowed=D(source['withdrawals'][b.identity][SPUSDS])
        if borrowed<0 or borrowed>paid+D('1e-8'):
            raise ValueError('Source origin exceeds actual reallocated cash')
        proofs.append({'transaction':b.identity,'source_account':SPUSDS,
                       'cash_reallocated':str(paid),'verified_borrowed_contribution':str(min(paid,borrowed)),
                       'unclassified_cash':str(max(D(0),paid-borrowed))})
    return proofs,source
