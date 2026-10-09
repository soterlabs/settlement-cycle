"""Authenticate two governance draws sent through the Spark subproxy to L2s.

February 20, 2025 spell: 300m, split between Arbitrum (200m) and Base (100m).
January 15, 2026 spell: 350m, split between Arbitrum (250m) and Optimism (100m).
https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20250220/SparkEthereum_20250220.sol
https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20260115/SparkEthereum_20260115.sol

Arbitrum retryable identities and OP-stack relay hashes prove the deliveries.
The January execution ALSO distributes earned reserve income: retain those
positions, but give them none of this explicitly borrowed bridge funding.
"""
from dataclasses import replace
from decimal import Decimal as D

from .spark_reserve_gifts import recognize_spark_reserve_gifts

ILK = '0x414c4c4f4341544f522d535041524b2d41000000000000000000000000000000'
SUFFIX = ':spark-arbitrum-spells'
# source tx, block, timestamp, draw; (chain, tx, block, timestamp, account, cost, value)
ROUTES = (
    ('0x395e70dfbb3b3a23fbfd0e7a4ad659c77302e2f5923606e006e981097cc27ef9', 21916640, 1740405623, D('300000000'), (
        ('base', '0x5802288d01441f44240d25501156ef4d640d2bb385c14b5f0862bb607bb75f43', 26808230, 1740405807,
         'base:0x2917956eff0b5eaf030abdb4ef4296df775009ca:0x5875eee11cf8398102fdad704c9e96607675467a', D('100000000'), D('100000035.9445265292616986822')),
        ('arbitrum', '0xba14a84914c8179a53152425e487041c26338b844ef515ffda51970cc0beb162', 309427868, 1740405995,
         'arbitrum:0x92afd6f2385a90e44da3a8b60fe36f6cbe1d8709:0x6491c05a82219b8d1479057361ff1654749b876b', D('100000000'), D('100000000.0')),
        ('arbitrum', '0xa086a99da04a206f35137f8dd2224db63258a5f072b2eeed6af535461ddf3b2e', 309427869, 1740405995,
         'arbitrum:0x92afd6f2385a90e44da3a8b60fe36f6cbe1d8709:0xddb46999f8891663a8f2828d25298f70416d7610', D('100000000'), D('100000074.2853690680170654885')),
    )),
    ('0x311bb97ca6fe9688c5dd235fcde093829720b0e1933ef57e616fc0703e1d90e3', 24269286, 1768831307, D('350000000'), (
        ('optimism', '0x687246d6f335579ad8fc18d223ab8218031b01e305b3daab63d16ca632477b11', 146616304, 1768831385,
         'optimism:0x876664f0c9ff24d1aa355ce9f1680ae1a5bf36fb:0xb5b2dc7fd34c249f4be7fb1fcea07950784229e0', D('100000000'), D('100000008.9545011207574636007')),
        ('arbitrum', '0x6ecac4fb59666b2697aa1617293d00c074bcb72ad5a0f42f3cdfc1e430005e14', 423020159, 1768831718,
         'arbitrum:0x92afd6f2385a90e44da3a8b60fe36f6cbe1d8709:0xddb46999f8891663a8f2828d25298f70416d7610', D('250000000'), D('250000126.8554590501638622604')),
    )),
)


def link_spark_arbitrum_spells(history):
    from ..normalize.allocation_capital import AssetMovement

    # Idempotent, and ownership-scoped even for shared multi-prime spells.
    history = recognize_spark_reserve_gifts(history)
    index = {b.identity: b for b in history.batches}
    if len(index) != len(history.batches):
        raise ValueError('Duplicate capital transaction')
    if not any(ILK in b.minted_by_ilk for b in history.batches):
        return history
    mapping = dict(history.venue_accounts)
    extras = set(history.analytics_only_venues)
    changed = False
    for tx, block, stamp, draw, legs in ROUTES:
        source_id = 'ethereum:' + tx
        ids = [source_id, *(chain + ':' + dest for chain, dest, *_ in legs)]
        if any(i + SUFFIX in index for i in ids):
            if any(i in index for i in ids):
                raise ValueError('Cannot append raw events to linked Spark Arbitrum spell')
            continue
        source = index.get(source_id)
        if source is None:
            continue
        if (source.chain != 'ethereum' or source.block != block or source.timestamp != stamp
                or abs(source.minted - draw) > D('1e-8')
                or set(source.minted_by_ilk) != {ILK}
                or source.minted_by_ilk[ILK] != source.minted
                or sum((leg[5] for leg in legs), D(0)) != draw
                or any(abs(m.change - m.external_income) > D('1e-8') for m in source.movements)):
            raise ValueError('Spark Arbitrum spell draw or existing custody differs')
        movements = list(source.movements)
        for chain, dest, dest_block, dest_stamp, account, cost, value in legs:
            claim = 'native-bridge:spark:arbitrum-spells:' + dest
            venue = 'S_SPELL_SEED_PENDING_' + chain.upper() + '_' + str(dest_block)
            if venue in mapping and mapping[venue] != claim:
                raise ValueError('Conflicting Spark Arbitrum spell claim ownership')
            mapping[venue] = claim
            extras.add(venue)
            movements.append(AssetMovement(claim, D(0), cost))
            dest_id = chain + ':' + dest
            destination = index.get(dest_id)
            if destination is None:
                continue  # Pinned before delivery: retain an in-flight claim.
            if (destination.chain != chain or destination.block != dest_block
                    or destination.timestamp != dest_stamp or dest_stamp <= stamp
                    or destination.minted or len(destination.movements) != 1
                    or destination.movements[0].account != account
                    or destination.movements[0].external_income
                    or abs(destination.movements[0].change - value) > D('1e-8')):
                raise ValueError('Spark Arbitrum spell receipt differs')
            index[dest_id + SUFFIX] = replace(destination, identity=dest_id + SUFFIX,
                movements=(*destination.movements, AssetMovement(claim, value, -value, preserve_basis=True)))
            del index[dest_id]
        index[source_id + SUFFIX] = replace(source, identity=source_id + SUFFIX, movements=tuple(movements))
        del index[source_id]
        changed = True
    if not changed:
        return history
    return replace(history, batches=tuple(index.values()), venue_accounts=mapping,
                   analytics_only_venues=tuple(sorted(extras)))
