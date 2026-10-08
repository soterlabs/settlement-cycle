"""Keep the independently witnessed May 2 Sky and saver token routes separate.

https://etherscan.io/tx/0xe087909f67a0f7ba0f093e0eac21b2a7bc573ff4cc4bdc8cee780e0a0667b7f7
Full receipt and old normalized batch: spark_distinct_saver_and_sky_funding.json.
The ALM starts/ends with zero USDS. Its sole USDS ingress is the Sky buffer,
and its sole egress is the same amount deposited into sUSDS. Independent USDT
Savings Take and Morpho withdrawal legs cannot finance that deposit. This
exact historical split does not classify saver cash as income, seed an opening
loan, or decide the principal/interest split on saver repayments.
"""
from dataclasses import replace
from decimal import Decimal as D

TX = 'ethereum:0xe087909f67a0f7ba0f093e0eac21b2a7bc573ff4cc4bdc8cee780e0a0667b7f7'
PREFIX = 'ethereum:0x1601843c5e9bc251a3272907010afa41fa18347e:'
USDS = PREFIX+'0xdc035d45d973e3ec169d2276ddab16f1e407384f'
SUSDS = PREFIX+'0xa3931d71877c0e7a3148cb7eb4463524fec27fbd'
USDT = PREFIX+'0xdac17f958d2ee523a2206206994597c13d831ec7'
MORPHO = PREFIX+'0xc7cdcfdefc64631ed6799c95e3b110cd42f2bd22'
ILK = '0x414c4c4f4341544f522d535041524b2d41000000000000000000000000000000'
PAID = D('180000066.289061952380736771')
SKY, SAVER = TX+':sky-usds-route', TX+':saver-usdt-route'


def separate_spark_savings_routes(history):
    if SUSDS not in history.venue_accounts.values():
        return history
    by_id = {b.identity:b for b in history.batches}
    if SKY in by_id or SAVER in by_id:
        if TX in by_id or SKY not in by_id or SAVER not in by_id:
            raise ValueError('Incomplete or mixed Spark funding-route history')
        return history
    b = by_id.get(TX)
    if b is None:
        return history
    movements = {m.account:m for m in b.movements}
    if (b.chain != 'ethereum' or b.block != 25007787 or b.timestamp != 1777730951
            or str(b.day) != '2026-05-02' or len(b.movements) != 4
            or set(movements) != {USDS,SUSDS,USDT,MORPHO}
            or set(b.minted_by_ilk) != {ILK} or b.minted_by_ilk[ILK] != b.minted
            or abs(b.minted-PAID)>D('1e-18')):
        raise ValueError('Spark funding-route metadata or debt differs from witness')
    cash, deposit = movements[USDS],movements[SUSDS]
    if (cash.value_before or cash.change or cash.external_income or cash.preserve_basis
            or deposit.change != PAID or deposit.external_income or deposit.preserve_basis
            or deposit.value_before != D('1429152143.171951537195093287')):
        raise ValueError('Spark USDS route differs from witnessed sole ingress/deposit')
    # Retain every original field, including corrected Morpho fee income. Only
    # the clearing boundary changes. These branches have disjoint accounts.
    sky = replace(b,identity=SKY,movements=(cash,deposit))
    saver = replace(b,identity=SAVER,minted=D(0),minted_by_ilk={},log_index=b.log_index+1,
                    movements=tuple(m for m in b.movements if m.account not in {USDS,SUSDS}))
    return replace(history,batches=tuple(x for old in history.batches
                   for x in ((sky,saver) if old.identity == TX else (old,))))
