"""Spark's spell-authorized B2C2 wallet as a capital boundary.

November 27, 2025 spell authorizes this exact wallet for USDC/USDT/PYUSD:
https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20251127/SparkEthereum_20251127.sol
Under the operator's EOA policy, paid cash enters the allocation and same-wallet
returns release its funded principal. No tracing of commingled wallet contents.
The reviewed stablecoin history leaves 1,451 USD outstanding; it is not silently
written off as an inferred conversion fee or counted as an idle exemption.
"""
from dataclasses import replace
from decimal import Decimal as D

HOLDER = '0x1601843c5e9bc251a3272907010afa41fa18347e'
ENTRY = '0xa29e963992597b21bcdcaa969d571984869c4ff5'
VENUE = 'S_B2C2_ENTRY'
ACCOUNT = 'eoa-allocation:ethereum:' + VENUE
SUFFIX = ':spark-b2c2-boundary'
# Exact transfer transaction, block, time, token, signed USD paid into entrypoint.
EVENTS = (
    ('0xbadae57b8a23ccbe32db53a513a9f597cd03fa6548952b5f7e7bab7508e02449', 23929157, 1764719351,
     '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', D('100')),
    ('0xde311a41fd461ec95c9ba504ceba5aaae54f7ab5e65a73abefe4d468f137419d', 23998084, 1765561175,
     '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', D('-10')),
    ('0xa43845086647fdcc273acbdd91ba73faa1e04e5a03d42fbed70458596a0f043c', 23998086, 1765561199,
     '0xdac17f958d2ee523a2206206994597c13d831ec7', D('-10')),
    ('0xac72f82538357f522fb40e12f1059c652f4dba31e1fb64d64b1ace9ffc6407b4', 23998368, 1765564607,
     '0xdac17f958d2ee523a2206206994597c13d831ec7', D('5')),
    ('0x87d87f42f6dbd5a72f51dbeacc747a52cba275d3e9619396b060700ecf7db4fc', 24937284, 1776882755,
     '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', D('100000')),
    ('0x163cd855925f958ff2d7d5e125185af0af961f1f7f191be6cec0b1e488e9ee7b', 24937537, 1776885827,
     '0xdac17f958d2ee523a2206206994597c13d831ec7', D('-100000')),
    ('0x78fdb0314ba0dcd111e9120006925f8ae32b5e1969871e6e178d6722f8e3aa08', 24937574, 1776886271,
     '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', D('1000000')),
    ('0x50f97d2b56db41030af13681323d146360ffbe0cffac9a780954c89c4a52c0dc', 24937720, 1776888023,
     '0xdac17f958d2ee523a2206206994597c13d831ec7', D('-999314')),
    ('0x96329886520c2120b55cceb9ea4b55972da979e61c493c759a0d2e7980538d00', 24938089, 1776892499,
     '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', D('1000000')),
    ('0x3ae986a47cce5e05b0ac3cacd404be466a7a042207360e8ed30524d1921dba4d', 24938157, 1776893315,
     '0xdac17f958d2ee523a2206206994597c13d831ec7', D('-999320')),
)


def link_spark_b2c2_boundary(history):
    from ..normalize.allocation_capital import AssetMovement

    index = {b.identity: b for b in history.batches}
    if len(index) != len(history.batches):
        raise ValueError('Duplicate capital transaction')
    ids = ['ethereum:' + e[0] for e in EVENTS]
    if any(i + SUFFIX in index for i in ids):
        if any(i in index for i in ids):
            raise ValueError('Cannot append raw events to linked Spark B2C2 history')
        return history
    if not any(i in index for i in ids):
        return history
    if VENUE in history.venue_accounts:
        raise ValueError('Spark B2C2 entrypoint already assigned')
    balance = D(0)
    for tx, block, stamp, token, paid in EVENTS:
        identity = 'ethereum:' + tx
        b = index.get(identity)
        if b is None:
            continue
        cash = [m for m in b.movements if m.account == f'ethereum:{HOLDER}:{token}']
        if (b.chain != 'ethereum' or b.block != block or b.timestamp != stamp
                or len(cash) != 1 or cash[0].external_income):
            raise ValueError('Spark B2C2 transfer differs from reviewed cash boundary')
        if paid > 0:
            available = b.minted - sum((m.change - m.external_income for m in b.movements), D(0))
            if cash[0].change > 0 or available < paid - D('1e-8'):
                raise ValueError('Spark B2C2 payment lacks observed funding')
        elif (b.minted or len(b.movements) != 1 or cash[0].change != -paid or balance < -paid):
            # No observed excess in these reviewed returns. Do not turn a
            # missing prior deposit into presumed earned income.
            raise ValueError('Spark B2C2 return lacks observed outstanding principal')
        index[identity + SUFFIX] = replace(b, identity=identity + SUFFIX,
            movements=(*b.movements, AssetMovement(ACCOUNT, balance, paid, preserve_basis=True)))
        del index[identity]
        balance += paid
    return replace(history, batches=tuple(index.values()),
                   venue_accounts={**history.venue_accounts, VENUE: ACCOUNT},
                   analytics_only_venues=(*history.analytics_only_venues, VENUE))
