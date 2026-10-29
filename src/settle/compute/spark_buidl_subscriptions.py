"""Spark's seven initial BUIDL subscriptions, linked at the issuer boundary.

The April 3, 2025 spell authorizes this USDC deposit EOA and BUIDL token:
https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20250403/SparkEthereum_20250403.sol
Canonical payments and subsequent deliveries are pinned in the fixture. Each
subscription finishes before the next payment; daily distributions are excluded.
The difference between cash paid and token face is retained in acquisition
basis, not asserted to be a particular fee or booked as additional revenue.
"""
from dataclasses import replace
from decimal import Decimal as D

HOLDER = '0x1601843c5e9bc251a3272907010afa41fa18347e'
USDC = '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
TOKEN = '0x6a9da2d710bb9b700acde7cb81f10f1ff8c89041'
DEPOSIT_WALLET = '0xd1917664be3fdaea377f6e8d5bf043ab5c3b1312'
CASH = f'ethereum:{HOLDER}:{USDC}'
SHARES = f'ethereum:{HOLDER}:{TOKEN}'
SUFFIX = ':spark-buidl-subscription'
# Payment tx/block/USDC, delivery tx/block/BUIDL, original income classification.
SUBSCRIPTIONS = (
    ('0x1cdbbb0361800340f00df09108629910a079e2827c62a221e242a9fbf951bfa5', 22217549, D('100000'),
     '0x5a1da3effdc1ecbc137e0e3bf31fb099a293d5c9a6bcfc60134f7dc788acb0f4', 22218905, D('100000'), D('100000')),
    ('0x11b6eca60f7d42b82d4d79fb4ed9c5921c59ea7b19927d34a38c48acb2f089c0', 22223720, D('50000000'),
     '0x1fe7b41ad1dff7cc2cc85ea9c96f32100d94583a94757e578a30ca5c1690f55a', 22226064, D('49949924.88'), D('0')),
    ('0x8d51766f0fa26063d13c7a471b3283624526002dc87279b63042e279d62b0939', 22230931, D('150000000'),
     '0xc14aa8ed1e707693eec217f0fe262f021df2cc1c2e0de6e38895ec52c32a8e10', 22233234, D('149850149.85'), D('0')),
    ('0xece9e141aced220cd0efa27db4db4528fb23850db9dd4bd895b7c498980fe6b6', 22238044, D('150000000'),
     '0x3b64c2bb0e8c7a3c103d47636c323d9be9d858c17a11a5a644594e05485724f0', 22240411, D('149880149.85'), D('0')),
    ('0xc4bd19168a3f2c5f5d12a357cbf076e872213bd12d10037b6691d50e2e176dcc', 22245282, D('150000000'),
     '0x69d80a94baa453301a917779a75295fbaca8038425e6d012a3253bb6ae178709', 22247584, D('149865149.85'), D('0')),
    ('0x7e52cfcc9c64c498f4420898abe7601d641a9341450072b209041ba23e4a6c54', 22382500, D('150000000'),
     '0x0fd9195c7f63a654513f9e0c7a3bb9b0ede4a9345d6fa9f8afc0e62802f5a673', 22383739, D('149940000'), D('0')),
    ('0x63885053e4a4bd9fb1e271a491dd2caf3e7e54f9fe3f73661102c16cdd7fd68d', 22389524, D('150000000'),
     '0xa07d591cb9b5f350cbb589bbc9d9005016aa346fa0a1cb4a4fc7803a7dad77c8', 22390880, D('149940000'), D('0')),
)


def link_spark_buidl_subscriptions(history):
    from ..normalize.allocation_capital import AssetMovement

    if SHARES not in history.venue_accounts.values():
        return history
    index = {b.identity: b for b in history.batches}
    if len(index) != len(history.batches):
        raise ValueError('Duplicate capital transaction')
    custody = {v: list(accounts) for v, accounts in history.custody_accounts.items()}
    venue = next(v for v, a in history.venue_accounts.items() if a == SHARES)
    for pay_tx, pay_block, paid, issue_tx, issue_block, delivered, income in SUBSCRIPTIONS:
        payment_id, issue_id = 'ethereum:' + pay_tx, 'ethereum:' + issue_tx
        if any(key + SUFFIX in index for key in (payment_id, issue_id)):
            if any(key in index for key in (payment_id, issue_id)):
                raise ValueError('Cannot append raw events to linked Spark BUIDL subscription')
            continue
        payment, issue = index.get(payment_id), index.get(issue_id)
        if payment is None:
            if issue is not None:
                raise ValueError('Spark BUIDL delivery lacks its funded subscription')
            continue
        cash = [m for m in payment.movements if m.account == CASH]
        available = payment.minted - sum(
            (m.change - m.external_income for m in payment.movements), D(0))
        if (payment.chain != 'ethereum' or payment.block != pay_block
                or len(cash) != 1 or cash[0].external_income or cash[0].change > 0
                or available < paid - D('.000000001')):
            raise ValueError('Spark BUIDL subscription lacks its normalized cash funding')
        account = 'subscription:ethereum:spark:buidl:' + issue_tx
        index[payment_id + SUFFIX] = replace(payment, identity=payment_id + SUFFIX,
            movements=(*payment.movements, AssetMovement(account, D(0), paid, preserve_basis=True)))
        del index[payment_id]
        if account not in custody.setdefault(venue, []):
            custody[venue].append(account)
        if issue is None:
            continue  # Keep paid, undelivered capital assigned to S19 at a cutoff.
        if (issue.chain != 'ethereum' or issue.block != issue_block
                or issue.timestamp <= payment.timestamp or issue.minted
                or len(issue.movements) != 1 or issue.movements[0].account != SHARES
                or issue.movements[0].change != delivered
                or issue.movements[0].external_income != income):
            raise ValueError('Spark BUIDL delivery differs from reviewed issuance')
        # The initial $100k test is demonstrably purchased capital despite the
        # normalizer's size-based income label. This override affects tracing
        # only. Later genuine distributions retain their existing treatment.
        shares = replace(issue.movements[0], external_income=D(0))
        index[issue_id + SUFFIX] = replace(issue, identity=issue_id + SUFFIX,
            movements=(shares, AssetMovement(account, delivered, -delivered, preserve_basis=True)))
        del index[issue_id]
    return replace(history, batches=tuple(index.values()), custody_accounts=custody)
