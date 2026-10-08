"""USTB historical capital: actual subscription NAV and reviewed cash exits.

The April 3, 2025 Spark spell onboards the USTB subscription contract:
https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20250403/SparkEthereum_20250403.sol

Six atomic Subscribe events pay 300m USDC and mint 28.190693m shares. The
legacy const_one tracing snapshot prices those shares in share units, not USD.
Pinned reads of the token's own real-time oracle reprice all eight movements.
Two OffchainRedeem burns match same-day direct cash payments to the cent at
that execution NAV. These are explicitly reviewed associations, not shared
on-chain request IDs or an automatic amount/time matcher. All receipts,
subscription payments, token burns and historical oracle reads are frozen in
spark_ustb_capital.json. No reported revenue or global debt is changed.
"""
from dataclasses import replace
from decimal import Decimal as D

HOLDER = '0x1601843c5e9bc251a3272907010afa41fa18347e'
TOKEN = '0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e'
ACCOUNT = f'ethereum:{HOLDER}:{TOKEN}'
CASH = f'ethereum:{HOLDER}:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
SUFFIX = ':spark-ustb'
# Transaction, block, timestamp, pre-transaction share units, share change, USD NAV.
MARKS = (
    ('0xda8e56b6c58868fa0383eb972d7a9ab9ba2cf54dd9dd96093f06c63f7e65330e', 22217607, 1744035959,
     D('0.00'), D('9406.100684'), D('10.631398')),
    ('0x8ff72c81166602bf0a80d82600ffe6d63632bf77904170d99c858bd9acb307f0', 22217661, 1744036607,
     D('9406.100684'), D('4693640.709422'), D('10.631406')),
    ('0x6d4820ff6bd5ded1d5e4c1a31fd6c8b83460f98a733ec1fa3b78a314f59c2922', 22223732, 1744109879,
     D('4703046.810106'), D('4702610.663715'), D('10.632392')),
    ('0x58dbad258c5e1197a6e8912a91a1fbd1d0c779d0bc51182a3e8004fe9163a530', 22267115, 1744632347,
     D('9405657.473821'), D('4699339.103745'), D('10.639794')),
    ('0xf832fe94123aab15b2ba130f6789dfa3b0fd2aee74778f2ed28604130fd73ece', 22318172, 1745248067,
     D('14104996.577566'), D('4695546.405709'), D('10.648388')),
    ('0x376f56bdbd5b0c2bfcc522ca59a4d11d3b66db1126ddfffc79256b19dfa48791', 22324620, 1745325803,
     D('18800542.983275'), D('9390150.126903'), D('10.649457')),
    ('0xc235bec8032617180c9e38d7b914592910ff9330a2fc9327820b189d5f1f3e9d', 22483471, 1747250831,
     D('28190693.110178'), D('-10.00'), D('10.675986')),
    ('0x49eefbb55bcae635426ff7994dbcbe4c853cb0f230f6856a27be64b0e1ead466', 22939134, 1752757859,
     D('28190683.110178'), D('-28190683.110178'), D('10.752185')),
)
# OffchainRedeem source, observed cash delivery, actual USDC, delivery block/time.
RETURNS = (
    ('0xc235bec8032617180c9e38d7b914592910ff9330a2fc9327820b189d5f1f3e9d',
     '0x4967b2260d6f934a5c4b5c8374dde7c1102a33d955e598113be30ea051c16151',
     D('106.76'), 22483489, 1747251047),
    ('0x49eefbb55bcae635426ff7994dbcbe4c853cb0f230f6856a27be64b0e1ead466',
     '0x056f664613a69e7785c321a3bea1bc0c047967f79aefcee808d533f240103480',
     D('303111440.08'), 22941712, 1752788891),
)


def link_spark_ustb(history):
    from ..normalize.allocation_capital import AssetMovement

    if ACCOUNT not in history.venue_accounts.values():
        return history
    by_id = {b.identity: b for b in history.batches}
    identities = {'ethereum:' + row[0] for row in MARKS} | {'ethereum:' + row[1] for row in RETURNS}
    if any(identity + SUFFIX in by_id for identity in identities):
        if identities & by_id.keys():
            raise ValueError('Cannot append raw events to linked Spark USTB history')
        return history
    replacements = {}
    custody = {v: list(accounts) for v, accounts in history.custody_accounts.items()}
    for tx, block, stamp, before, change, nav in MARKS:
        identity = 'ethereum:' + tx
        b = by_id.get(identity)
        if b is None:
            continue
        found = [m for m in b.movements if m.account == ACCOUNT]
        if b.chain != 'ethereum' or b.block != block or b.timestamp != stamp or len(found) != 1:
            raise ValueError('USTB historical position metadata differs')
        m = found[0]
        if m.external_income or m.preserve_basis:
            raise ValueError('Unexpected USTB gift or custody flag')
        expected_before, expected_change = before * nav, change * nav
        legacy = m.value_before == before and m.change == change
        current = (abs(m.value_before - expected_before) < D('1e-12')
                   and abs(m.change - expected_change) < D('1e-12'))
        if not (legacy or current):
            raise ValueError('USTB snapshot differs from legacy or verified NAV marks')
        fixed = replace(m, value_before=expected_before, change=expected_change)
        replacements[identity] = replace(b, identity=identity + SUFFIX,
            movements=tuple(fixed if x is m else x for x in b.movements))
    for burn, payment, cash, block, stamp in RETURNS:
        source_id, receipt_id = 'ethereum:' + burn, 'ethereum:' + payment
        source = replacements.get(source_id)
        if source is None:
            continue  # No opening claim or loan is inferred from a receipt alone.
        if source.minted or source.minted_by_ilk or len(source.movements) != 1:
            raise ValueError('USTB redemption source shape differs')
        m = source.movements[0]
        amount = -m.change
        if amount <= 0 or abs(amount - cash) > D('.005'):
            raise ValueError('USTB redemption differs from reviewed cash NAV')
        claim = 'issuer:spark:ustb:' + burn
        custody.setdefault('S21', []).append(claim)
        replacements[source_id] = replace(source, movements=(replace(m, preserve_basis=True),
            AssetMovement(claim, D(0), amount)))
        receipt = by_id.get(receipt_id)
        if receipt is None:
            continue
        if (receipt.chain != 'ethereum' or receipt.block != block or receipt.timestamp != stamp
                or receipt.minted or receipt.minted_by_ilk or len(receipt.movements) != 1):
            raise ValueError('USTB reviewed cash receipt metadata differs')
        received = receipt.movements[0]
        if received.account != CASH or received.change != cash or received.external_income:
            raise ValueError('USTB reviewed cash receipt amount differs')
        replacements[receipt_id] = replace(receipt, identity=receipt_id + SUFFIX,
            movements=(*receipt.movements, AssetMovement(claim, cash, -cash, preserve_basis=True)))
    return replace(history, batches=tuple(replacements.get(b.identity, b) for b in history.batches),
                   custody_accounts=custody)
