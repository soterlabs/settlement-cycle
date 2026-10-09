"""Reviewed actual Curve swap gains for previously pinned Grove histories.

Uses normalize/allocation_curve_swaps.py's event/transfer reconciliation.
Canonical events: tests/fixtures/grove_curve_swap_gain_events.json.
No reported revenue or debt changes.
"""
from dataclasses import replace
from decimal import Decimal

GAINS = (
    ('0x9deb8821c59991e7ab727bbafa3d92291ee61b8db09d808dc97534174a428b5a', 24048685, 1766171975, 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', Decimal('40001.140039'), Decimal('1.140039')),
    ('0x0c6f1e5b25a0d2258e36bb1a62dd08c5fe9533cc40869eb73e62017905547689', 24185791, 1767824951, 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', Decimal('210013.691366'), Decimal('13.691366')),
    ('0x03f1b1ae56cca4e0ae41fb4a9e4159a24ba63817980baa1122d12ecf4f152c3b', 24234162, 1768408403, 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0x8292bb45bf1ee4d140127049757c2e0ff06317ed', Decimal('1288.039377867903403401'), Decimal('88.039377867903403401')),
    ('0x78d4c9781e0dae367269ebd787f7b3926386cafc62320f0c66be6111e1b969b0', 24234766, 1768415687, 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0x8292bb45bf1ee4d140127049757c2e0ff06317ed', Decimal('1224.726953876311086135'), Decimal('124.726953876311086135')),
    ('0x0b2f4a0d6f70523e23ac6002f263a883203f47bc0ac7809147ef8d675d737dc8', 24234884, 1768417103, 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0x8292bb45bf1ee4d140127049757c2e0ff06317ed', Decimal('111.696479775133926617'), Decimal('111.696479775133926617')),
    ('0x7bbb929010e3fd973ef7f93db9404bc131323d4fa5b726eb8d266581f6f9b140', 24235177, 1768420619, 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0x8292bb45bf1ee4d140127049757c2e0ff06317ed', Decimal('2.401592178165848086'), Decimal('2.401592178165848086')),
    ('0x05248491efa9728fe3bd05232d316125ba0822748799056d72a492b21b40e4ee', 25446902, 1783020311, 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', Decimal('2000283.35832'), Decimal('283.35832')),
    ('0xef6547d6761747a00ede93400e86f1293289919cadbb7be104328c62ad0a1501', 25496009, 1783611971, 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', Decimal('2000176.375711'), Decimal('176.375711')),
    ('0x13a7f7f40586185260aa0b0820173940760b06cda2de16bf68a626d8ffc0559e', 25496180, 1783614023, 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', Decimal('5000221.302642'), Decimal('221.302642')),
    ('0x5af34986f2b7b88872893930b9c8213d488026ddc2ec167d59b0576f6ed0fc2e', 25531889, 1784044223, 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', Decimal('5000101.035805'), Decimal('101.035805')),
)


def recognize_grove_curve_swap_gains(history):
    index = {b.identity: b for b in history.batches}
    if len(index) != len(history.batches):
        raise ValueError('Duplicate Curve capital transaction')
    for tx, block, timestamp, account, change, gain in GAINS:
        b = index.get('ethereum:' + tx)
        if b is None:
            continue
        ms = [m for m in b.movements if m.account == account]
        if (b.chain != 'ethereum' or b.block != block or b.timestamp != timestamp
                or len(ms) != 1 or ms[0].change != change
                or ms[0].external_income not in (Decimal(0), gain)):
            raise ValueError('Curve gain differs from its reviewed cash movement')
        index[b.identity] = replace(b, movements=tuple(
            replace(m, external_income=gain) if m.account == account else m for m in b.movements))
    return replace(history, batches=tuple(index.values()))
