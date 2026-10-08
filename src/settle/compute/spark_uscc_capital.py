"""USCC subscription custody and delayed, reviewed cash settlement groups.

The October 16, 2025 Spark spell authorizes the exact USDC entrypoint and
USCC off-chain redemptions:
https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20251016/SparkEthereum_20251016.sol

Four payments buy three issuances for 150.01m USDC. Three OffchainRedeem
burns are paid in two later transfers at settlement-day NAV, not request-day
NAV. Both cash groups match that independent NAV within one cent. These are
reviewed associations, not shared on-chain request IDs or a runtime date/amount
matcher. Raw events, block-pinned NAV and normalized batches are frozen in the
spark_uscc_* fixtures. Earnings and NAV changes never create borrowed funding.
"""
from dataclasses import replace
from decimal import Decimal as D

HOLDER = '0x1601843c5e9bc251a3272907010afa41fa18347e'
TOKEN = '0x14d60e7fdc0d71d8611742720e4c50e7a974020c'
ACCOUNT = f'ethereum:{HOLDER}:{TOKEN}'
CASH = f'ethereum:{HOLDER}:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
SUFFIX = ':spark-uscc'
# Transaction, block, timestamp, share units before/change, request/issuance NAV.
MARKS = (
    ('0x9701dd940f4b4b211d9328cf6161c24078487f193ec56d9d8f055cb4bc325200', 23626140, 1761052163,
     D('0.00'), D('4428393.645144'), D('11.296558')),
    ('0x0a286c4057964fa5b275451fad781e584ac76ed3ad6dbd7e7aae73ff041725b5', 23633261, 1761138467,
     D('4428393.645144'), D('4424373.911272'), D('11.293034')),
    ('0x4f5c87581ef8b9a636aa07dde269910e3cb4389a9f1dd9ec04de3bdf6cfb53a6', 23733591, 1762351667,
     D('8852767.556416'), D('4412716.424986'), D('11.342953')),
    ('0xcae6ffe1b84fd4702788fa6117b1b3017e6598855408407c10854872c70a856d', 23919053, 1764597203,
     D('13265483.981402'), D('-4421827.9938'), D('11.369271')),
    ('0xca43c99cdc901368b271e7200ca949c2e50f6405b53b9a16207b567c294d0dd2', 23919081, 1764597551,
     D('8843655.987602'), D('-4421827.9938'), D('11.369271')),
    ('0x9c38168eaf28096721d2daa374cc8105e1408b364863f57faa05185c1ab37fb8', 23933973, 1764777695,
     D('4421827.993802'), D('-4421827.993802'), D('11.380015')),
)

SUBSCRIPTIONS = (
    ((
        ('0xfc298a3643627678a14071b5b5e7a9ddd2c8853a1030bd364a200fd59bcf1981', 23620219, 1760980343, D('10000')),
        ('0x5ac3e1a191b96b810beb7e5b5ddf52e6c4eeab1e0bd2c7b20526be4be970660e', 23620331, 1760981699, D('50000000')),
    ), '0x9701dd940f4b4b211d9328cf6161c24078487f193ec56d9d8f055cb4bc325200'),
    ((
        ('0xd9218cf442a60d710849f35fb9403fd66da840faaf41f0d1f0703d55156a0397', 23627633, 1761070283, D('50000000')),
    ), '0x0a286c4057964fa5b275451fad781e584ac76ed3ad6dbd7e7aae73ff041725b5'),
    ((
        ('0x76a6c50cc9909525a67fc286f4c84477f50976fe9bac7be78b37c6c51143ffa6', 23726901, 1762270751, D('50000000')),
    ), '0x4f5c87581ef8b9a636aa07dde269910e3cb4389a9f1dd9ec04de3bdf6cfb53a6'),
)

# Burn group, payment, amount, block, timestamp, independently quoted settlement NAV.
REDEMPTIONS = (
    (('0xcae6ffe1b84fd4702788fa6117b1b3017e6598855408407c10854872c70a856d','0xca43c99cdc901368b271e7200ca949c2e50f6405b53b9a16207b567c294d0dd2',),
     '0x6641633601e1b4c7364e7b5b497ebed27d76b31f6988edab1bb73b35b95c032c', D('100682211.13'), 23926724, 1764689915, D('11.384682')),
    (('0x9c38168eaf28096721d2daa374cc8105e1408b364863f57faa05185c1ab37fb8',),
     '0xb230b52789a38848821b049b93f2bfed9c4d84dc2eb32e0d4cbb1ae69d3623fc', D('50331740.13'), 23940496, 1764861227, D('11.382564')),
)


def link_spark_uscc(history):
    from ..normalize.allocation_capital import AssetMovement

    if ACCOUNT not in history.venue_accounts.values():
        return history
    by_id = {b.identity: b for b in history.batches}
    identities = {'ethereum:' + row[0] for row in MARKS}
    identities.update('ethereum:' + p[0] for payments,_ in SUBSCRIPTIONS for p in payments)
    identities.update('ethereum:' + r[1] for r in REDEMPTIONS)
    if any(identity + SUFFIX in by_id for identity in identities):
        if identities & by_id.keys():
            raise ValueError('Cannot append raw events to linked Spark USCC history')
        return history
    replacements = {}
    custody = {v:list(accounts) for v,accounts in history.custody_accounts.items()}
    for tx,block,stamp,before,change,nav in MARKS:
        identity = 'ethereum:' + tx
        b = by_id.get(identity)
        if b is None:
            continue
        if (b.chain != 'ethereum' or b.block != block or b.timestamp != stamp
                or b.minted or b.minted_by_ilk or len(b.movements) != 1):
            raise ValueError('USCC historical share metadata differs')
        m = b.movements[0]
        if m.account != ACCOUNT or m.external_income or m.preserve_basis:
            raise ValueError('Unexpected USCC historical share movement')
        expected_before,expected_change = before*nav,change*nav
        legacy = m.value_before == before and m.change == change
        current = abs(m.value_before-expected_before)<D('1e-12') and abs(m.change-expected_change)<D('1e-12')
        if not (legacy or current):
            raise ValueError('USCC snapshot differs from legacy or verified NAV marks')
        replacements[identity] = replace(b,identity=identity+SUFFIX,
            movements=(replace(m,value_before=expected_before,change=expected_change),))
    for payments,issue in SUBSCRIPTIONS:
        claim = 'issuer:spark:uscc:subscription:' + issue
        prior = D(0)
        seen = 0
        for tx,block,stamp,paid in payments:
            identity = 'ethereum:' + tx
            b = by_id.get(identity)
            if b is None:
                continue
            if (b.chain != 'ethereum' or b.block != block or b.timestamp != stamp
                    or abs(b.minted-paid)>D('1e-18')
                    or not any(m.account == CASH for m in b.movements)
                    or any(m.change or m.external_income for m in b.movements)):
                raise ValueError('USCC subscription draw/payment shape differs')
            replacements[identity] = replace(b,identity=identity+SUFFIX,
                movements=(*b.movements,AssetMovement(claim,prior,paid)))
            prior += paid
            seen += 1
        if not seen:
            continue  # Issuance alone stays unknown funding; never seed a loan.
        custody.setdefault('S22',[]).append(claim)
        identity = 'ethereum:' + issue
        issuance = replacements.get(identity)
        if issuance is not None:
            if seen != len(payments):
                raise ValueError('Incomplete USCC subscription funding group')
            value = issuance.movements[0].change
            # Receipt of the purchased shares releases ALL paid basis, even
            # when the contemporaneous quoted NAV is temporarily below cost.
            replacements[identity] = replace(issuance,movements=(*issuance.movements,
                AssetMovement(claim,value,-value,preserve_basis=True)))
    for burns,payment,cash,block,stamp,settlement_nav in REDEMPTIONS:
        claim = 'issuer:spark:uscc:redemption:' + payment
        prior,shares = D(0),D(0)
        seen = 0
        for burn in burns:
            identity = 'ethereum:' + burn
            source = replacements.get(identity)
            if source is None:
                continue
            m = source.movements[0]
            amount = -m.change
            shares += -next(row[4] for row in MARKS if row[0] == burn)
            replacements[identity] = replace(source,movements=(replace(m,preserve_basis=True),
                AssetMovement(claim,prior,amount)))
            prior += amount
            seen += 1
        if not seen:
            continue
        custody.setdefault('S22',[]).append(claim)
        identity = 'ethereum:' + payment
        receipt = by_id.get(identity)
        if receipt is not None:
            if seen != len(burns) or abs(shares*settlement_nav-cash)>D('.01'):
                raise ValueError('Incomplete USCC redemption or settlement NAV mismatch')
            if (receipt.chain != 'ethereum' or receipt.block != block or receipt.timestamp != stamp
                    or receipt.minted or receipt.minted_by_ilk or len(receipt.movements)!=1):
                raise ValueError('USCC reviewed cash receipt metadata differs')
            m = receipt.movements[0]
            if m.account != CASH or m.change != cash or m.external_income:
                raise ValueError('USCC reviewed cash receipt amount differs')
            # Mark the grouped receivable at actual delivery. The change from
            # request-day NAV is a gain/loss, never additional borrowed basis.
            replacements[identity] = replace(receipt,identity=identity+SUFFIX,
                movements=(*receipt.movements,AssetMovement(claim,cash,-cash,preserve_basis=True)))
    return replace(history,batches=tuple(replacements.get(b.identity,b) for b in history.batches),
                   custody_accounts=custody)
