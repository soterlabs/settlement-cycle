"""Identify the spell's 400m Sky-funded Optimism/Unichain seed allocation.

May 29, 2025 spell, executed June 2: draw 400m USDS, wrap 200m in sUSDS,
then bridge 100m USDS and 100m-cost sUSDS to each destination ALM:
https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20250529/SparkEthereum_20250529.sol

Capital goes from allocator buffer through the SUBPROXY, bypassing Ethereum's
ALM. Canonical cash legs and exact cross-domain relay hashes support these
four receipts; there is no synthetic opening balance or amount/date matcher.
"""
from dataclasses import replace
from decimal import Decimal as D

SOURCE = 'ethereum:0x4ddc25f122e8092d40a008e2a1807ed81beb514f054345085ef863cdf266cf5e'
SUFFIX = ':spark-native-seed'
ILK = '0x414c4c4f4341544f522d535041524b2d41000000000000000000000000000000'
COST = D('100000000')
# chain, actual recipient, token, authenticated mint transaction, block, time, value
SEEDS = (
    ('optimism', '0x876664f0c9ff24d1aa355ce9f1680ae1a5bf36fb', '0x4f13a96ec5c4cf34e442b46bbd98a0791f20edc3',
     '0xbaaa64bad91540a8d9c1a839c47bb82ab7a9ccb40005760a6dc7af4d0d9c4762', 136638485, 1748875747, D('100000000')),
    ('optimism', '0x876664f0c9ff24d1aa355ce9f1680ae1a5bf36fb', '0xb5b2dc7fd34c249f4be7fb1fcea07950784229e0',
     '0x65e88eccf2ab4795030328764ec1ef1cb0af24386a6d502ae41b3412985737a2', 136638485, 1748875747, D('100000011.7244374421593087022')),
    ('unichain', '0x345e368fccd62266b3f5f37c9a131fd1c39f5869', '0x7e10036acc4b56d4dfca3b77810356ce52313f9c',
     '0xc8bf2bf8292c5d37009f1c48d9de7fa04d3d8da2b21d1c15251b6a9b7d9fc75b', 18127354, 1748875713, D('100000000')),
    ('unichain', '0x345e368fccd62266b3f5f37c9a131fd1c39f5869', '0xa06b10db9f390990364a3984c04fadf1c13691b5',
     '0x8463716d885fc1a2ab1dad141a3e710fc9d1b41a5c7d741dcd0b74852f2d3484', 18127354, 1748875713, D('100000006.6996783699817514919')),
)


def link_spark_native_seed(history):
    from ..normalize.allocation_capital import AssetMovement

    by_id = {b.identity:b for b in history.batches}
    if SOURCE+SUFFIX in by_id or SOURCE not in by_id:
        return history
    source = by_id[SOURCE]
    if (source.block != 22617678 or source.timestamp != 1748875655
            or abs(source.minted-4*COST) > D('1e-8')
            or set(source.minted_by_ilk) != {ILK}
            or source.minted_by_ilk[ILK] != source.minted
            or source.movements):
        raise ValueError('Spark native seed draw or existing custody differs')
    mapping = dict(history.venue_accounts)
    extras = set(history.analytics_only_venues)
    movements, replacements = [], {}
    for n,(chain,holder,token,tx,block,stamp,value) in enumerate(SEEDS):
        claim = f'native-bridge:spark:seed:{chain}:{tx}'
        venue = f'S_NATIVE_SEED_PENDING_{n+1}'
        if venue in mapping and mapping[venue] != claim:
            raise ValueError('Conflicting Spark seed claim ownership')
        mapping[venue] = claim
        extras.add(venue)
        movements.append(AssetMovement(claim,D(0),COST))
        destination = by_id.get(chain+':'+tx)
        if destination is None:
            continue
        received = [m for m in destination.movements if m.account == f'{chain}:{holder}:{token}']
        if (destination.block != block or destination.timestamp != stamp or destination.minted
                or len(received) != 1 or received[0].external_income
                or abs(received[0].change-value) > D('1e-8')):
            raise ValueError('Spark native seed receipt differs')
        replacements[destination.identity] = replace(destination,identity=destination.identity+SUFFIX,
            movements=(*destination.movements,AssetMovement(claim,value,-value,preserve_basis=True)))
    replacements[SOURCE] = replace(source,identity=SOURCE+SUFFIX,movements=tuple(movements))
    return replace(history,batches=tuple(replacements.get(b.identity,b) for b in history.batches),
                   venue_accounts=mapping,analytics_only_venues=tuple(sorted(extras)))
