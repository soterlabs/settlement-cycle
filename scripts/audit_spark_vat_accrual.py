#!/usr/bin/env python3
"""Independently reconstruct Spark cash debt and non-cash Vat rate accrual.

Vat.fold adds Art * delta_rate to debt and surplus-buffer dai, not ALM cash.
Keep this separate from both allocation principal and grab-based MSC debt.
The exact Frob cash is checked against the saved capital history by transaction.
Vat.fold semantics (Art * delta_rate, credited to the surplus destination):
https://github.com/makerdao/dss/blob/fa4f6630afb0624d04a003e920b0d71a00331d98/src/vat.sol#L238
Historical Spark fee changes are explicit in the January 23 / February 6 spells:
https://github.com/sky-ecosystem/spells-mainnet/blob/17926e1879ca79d017c7e42525825907d8f673b1/archive/2025-01-23-DssSpell/DssSpell.sol#L113
https://github.com/sky-ecosystem/spells-mainnet/blob/17926e1879ca79d017c7e42525825907d8f673b1/archive/2025-02-06-DssSpell/DssSpell.sol#L124
Actual event deltas, not advertised annual rates, determine this audit.
"""
import argparse
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from settle.extract._keccak import keccak256
from settle.normalize.sources.hypersync_debt import _FROB_T0, _GRAB_T0, _VAT, _decode_dart

ILK = '0x414c4c4f4341544f522d535041524b2d41000000000000000000000000000000'
VOW = '0xa950524441892a31ebddf91d3ceefa04bf454466'
FOLD = '0x' + keccak256(b'fold(bytes32,address,int256)')[:4].hex() + '00'*28
RAD = 10**45


def word(raw, index):
    return int(raw.removeprefix('0x')[index*64:(index+1)*64], 16)


def reconstruct(evidence, debt_days):
    with localcontext() as ctx:
        ctx.prec = 90
        return _reconstruct(evidence, debt_days)


def _reconstruct(evidence, debt_days):
    opening, closing = evidence['states']
    call = '0x' + keccak256(b'ilks(bytes32)')[:4].hex() + ILK[2:]
    if any(r['calldata'] != call or len(r['result']) != 322 for r in (opening, closing)):
        raise ValueError('Invalid pinned Vat ilks ABI evidence')
    if opening['block'] != evidence['from_block']-1 or closing['block'] != evidence['pin']:
        raise ValueError('Vat state boundaries differ from event range')
    if word(opening['result'], 0) != 0:
        raise ValueError('Vat reconstruction requires zero opening Art')
    rate = word(opening['result'], 1)
    if rate <= 0:
        raise ValueError('Missing initialized opening Vat rate')
    art = cash = accrual = prior_grab = current_grab = 0
    start = min(r['day'] for r in debt_days)
    daily = {}
    folds = []
    counts = Counter()
    cash_by_tx = defaultdict(int)
    last_key = None
    for row in sorted(evidence['rows'], key=lambda r: (r['block_number'], r['log_index'])):
        key = row['block_number'], row['log_index']
        if key == last_key:
            raise ValueError('Duplicate Vat event')
        last_key = key
        if (row['address'] != _VAT or row['topic1'] != ILK
                or not evidence['from_block'] <= key[0] <= evidence['pin']):
            raise ValueError('Wrong Vat event contract, ilk or range')
        day = datetime.fromtimestamp(row['block_time'], UTC).date().isoformat()
        topic = row['topic0']
        if topic in (_FROB_T0, _GRAB_T0):
            dart = _decode_dart(row['data'])
            if topic == _FROB_T0:
                art += dart
                cash += dart*rate
                cash_by_tx[row['transaction_hash']] += dart*rate
                counts['frob'] += 1
            else:
                # Existing MSC control classifies positive grabs to the Vow.
                # Do not silently fold unrelated debt changes into this proof.
                if dart and (dart < 0 or '0x'+row['data'].removeprefix('0x')[128:][200:264][-40:] != VOW):
                    raise ValueError('Unclassified nonzero Grab')
                if day < start:
                    prior_grab += dart
                else:
                    current_grab += dart
                counts['grab'] += 1
        elif topic == FOLD:
            payload = row['data'].removeprefix('0x')[128:]
            raw = payload[136:200]  # third ABI argument after four-byte selector
            if (len(raw) != 64 or payload[:8] != FOLD[2:10]
                    or '0x'+payload[8:72] != ILK
                    or '0x'+payload[72:136] != row['topic2']
                    or '0x'+raw != row['topic3']):
                raise ValueError('Malformed Fold payload')
            delta = int(raw, 16)
            if delta >= 2**255:
                delta -= 2**256
            if '0x'+row['topic2'][-40:] != VOW:
                raise ValueError('Unexpected Fold surplus destination')
            amount = art*delta  # grab-origin accrual remains in MSC control
            accrual += amount
            rate += delta
            if amount:
                folds.append({'day':day,'block':key[0],'transaction':row['transaction_hash'],
                              'non_msc_accrual':str(D(amount)/RAD),'rate_after':str(D(rate)/10**27)})
            counts['fold'] += 1
        else:
            raise ValueError('Unknown Vat event type')
        if rate <= 0 or art < 0:
            raise ValueError('Invalid reconstructed Vat state')
        if art*rate != cash+accrual:
            raise ValueError('Vat cash/accrual identity broken')
        daily[day] = (rate, art, cash, accrual, prior_grab, current_grab)
    if rate != word(closing['result'],1) or art+prior_grab+current_grab != word(closing['result'],0):
        raise ValueError('Reconstructed Vat Art/rate differs from pinned RPC')
    observed = {r['transaction']:D(r['cash']) for r in evidence['capital_cash']}
    if len(observed) != len(evidence['capital_cash']):
        raise ValueError('Duplicate capital cash transaction')
    nonzero = {k:D(v)/RAD for k,v in cash_by_tx.items() if v}
    if nonzero.keys() != observed.keys() or any(abs(v-observed[k])>D('1e-18') for k,v in nonzero.items()):
        raise ValueError('Vat Frob cash differs from saved capital history')
    result = []
    for row in debt_days:
        eligible = [d for d in daily if d <= row['day']]
        if not eligible:
            raise ValueError('No Vat history before control day')
        r,a,c,f,g0,g1 = daily[max(eligible)]
        values = row['by_ilk'].get(ILK.removeprefix('0x'),row['by_ilk'].get(ILK))
        expected = {'debt':D((a+g0+g1)*r)/RAD,
                    'prior_msc_debt':D(g0*r)/RAD,'current_month_msc_debt':D(g1*r)/RAD}
        if values is None or any(abs(D(values[k])-v)>D('1e-18') for k,v in expected.items()):
            raise ValueError('Vat reconstruction differs from debt/MSC control')
        if D(values['rate']) != D(r)/10**27:
            raise ValueError('Vat reconstructed rate differs from daily control')
        result.append({'day':row['day'],'ilk':ILK,'cash_draws_less_repayments':str(D(c)/RAD),
                       'non_cash_rate_accrual':str(D(f)/RAD),'non_msc_debt':str(D(a*r)/RAD)})
    return {'scope':'Independent Vat event proof; no allocation principal or settlement change.',
            'counts':dict(counts),'first_non_cash_accrual':folds[0] if folds else None,
            'last_non_cash_accrual':folds[-1] if folds else None,'accrual_events':folds,'daily':result}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('evidence','debt-control','output'):
        p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args()
    with gzip.open(args.evidence,'rt') as f:
        evidence=json.load(f)
    result=reconstruct(evidence,json.loads(args.debt_control.read_text()))
    result['input_hashes']={str(x):hashlib.sha256(x.read_bytes()).hexdigest()
                            for x in (args.evidence,args.debt_control)}
    args.output.write_text(json.dumps(result,indent=2)+'\n')


if __name__ == '__main__':
    main()
