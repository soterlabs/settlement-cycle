"""July 6 Optimism/Unichain withdrawals, finalized on Ethereum July 13.

The July 2 spells burn the full USDS/sUSDS holdings for return to Ethereum:
https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20260702/SparkOptimism_20260702.sol
https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20260702/SparkUnichain_20260702.sol

Message payload hashes, portal withdrawal hashes and actual burn/escrow cash
legs authenticate each route. Carry source funding, never infer a new draw.
"""
from dataclasses import replace
from decimal import Decimal as D

ETH_ALM = '0x1601843c5e9bc251a3272907010afa41fa18347e'
SUFFIX = ':spark-op-uni-withdrawal'
# Chain, holder, source transaction/block/time; local token, remote token,
# raw units, receipt tx, source/arrival USD, receipt block/time.
ROUTES = (
    ('unichain', '0x345e368fccd62266b3f5f37c9a131fd1c39f5869',
     '0x33f66feba68e407fd92e5b4c46da5a53389096a5a6b9c40b8909f1a57986edda', 52612800, 1783361159, (
        ('0x7e10036acc4b56d4dfca3b77810356ce52313f9c', '0xdc035d45d973e3ec169d2276ddab16f1e407384f',
         99990828814818609701977623, '0xce77f64f1620b2c58bec71b05f5805b6ac3c2d4674ca808b0ec12fdda4a29907',
         D('99990828.814818609701977623'), D('99990828.814818609701977623'), 25526082, 1783974311),
        ('0xa06b10db9f390990364a3984c04fadf1c13691b5', '0xa3931d71877c0e7a3148cb7eb4463524fec27fbd',
         94737866160999974496590936, '0xdac1b1faa5a46aa7d327af557d8ebb7d94c09cb0417d35696791b45f15da458c',
         D('104413816.4847431723514177818'), D('104485643.1714990934628758982'), 25526084, 1783974335),
    )),
    ('optimism', '0x876664f0c9ff24d1aa355ce9f1680ae1a5bf36fb',
     '0x216a9de9b07a0c954dd797b6abf95c4ed0af6b1ba0f5156d0c40533fb656a16e', 153881194, 1783361165, (
        ('0x4f13a96ec5c4cf34e442b46bbd98a0791f20edc3', '0xdc035d45d973e3ec169d2276ddab16f1e407384f',
         100304256586332342515511286, '0x32c5a7912f97abe46a7c384f938c764a3719a9d20d90d063f765e4f2df05fe4a',
         D('100304256.586332342515511286'), D('100304256.586332342515511286'), 25526076, 1783974239),
        ('0xb5b2dc7fd34c249f4be7fb1fcea07950784229e0', '0xa3931d71877c0e7a3148cb7eb4463524fec27fbd',
         182561888353827758745421790, '0x23e6354a139fb301518266439c14a5f49c0a72dbc1b88ec3fc0b229a0ebedb74',
         D('201207650.9649300812260640099'), D('201346048.9736029324689054108'), 25526079, 1783974275),
    )),
)


def link_spark_op_uni_withdrawals(history):
    return _link_withdrawals(history, ROUTES, SUFFIX)


def link_spark_june_op_uni_withdrawals(history):
    from .spark_june_op_uni_routes import JUNE_ROUTES

    return _link_withdrawals(history, JUNE_ROUTES, ':spark-june-op-uni-withdrawal',
                             full_exit=False, venue_suffix='_JUNE')


def _link_withdrawals(history, routes, suffix, *, full_exit=True, venue_suffix=''):
    from ..normalize.allocation_capital import AssetMovement

    index = {b.identity: b for b in history.batches}
    if len(index) != len(history.batches):
        raise ValueError('Duplicate capital transaction')
    mapping, extras = dict(history.venue_accounts), set(history.analytics_only_venues)
    changed = False
    for chain, holder, source_tx, source_block, source_stamp, legs in routes:
        source_id = chain + ':' + source_tx
        identities = [source_id, *('ethereum:' + leg[3] for leg in legs if leg[3])]
        transformed = [source_id + suffix + f':{n+1}' for n in range(len(legs))]
        transformed.extend('ethereum:' + leg[3] + suffix for leg in legs if leg[3])
        if any(i in index for i in transformed):
            if any(i in index for i in identities):
                raise ValueError('Cannot append raw events to linked Spark OP/Uni withdrawal')
            continue
        source = index.get(source_id)
        if source is None:
            continue  # Unfunded receipts retain their original unresolved status.
        if (source.chain != chain or source.block != source_block
                or source.timestamp != source_stamp or source.minted or len(source.movements) != 2):
            raise ValueError('Spark OP/Uni withdrawal source differs')
        movements = source.movements
        for n, (local, remote, _, tx, source_value, arrival_value, block, stamp) in enumerate(legs):
            account = f'{chain}:{holder}:{local}'
            found = [m for m in movements if m.account == account]
            if (len(found) != 1 or found[0].external_income
                    or abs(found[0].change + source_value) > D('1e-8')
                    or (full_exit and abs(found[0].value_before - source_value) > D('1e-8'))
                    or found[0].value_before + D('1e-8') < source_value):
                raise ValueError('Spark OP/Uni withdrawal burn differs')
            claim_id = tx or source_tx + ':' + local
            claim = f'native-bridge:spark:{chain}:{claim_id}'
            venue = f'S_{chain.upper()}_NATIVE_PENDING{venue_suffix}_{n+1}'
            if venue in mapping and mapping[venue] != claim:
                raise ValueError('Conflicting Spark OP/Uni bridge ownership')
            mapping[venue] = claim
            extras.add(venue)
            # Separate authenticated token legs must not share a clearing
            # account: that would mix their borrowed/earned funding ratios.
            leg_id = source_id + suffix + f':{n+1}'
            index[leg_id] = replace(source, identity=leg_id, log_index=source.log_index + n,
                movements=(replace(found[0], preserve_basis=True), AssetMovement(claim, D(0), source_value)))
            if tx is None:
                continue  # Pinned portal state proves the withdrawal remains pending.
            destination_id = 'ethereum:' + tx
            destination = index.get(destination_id)
            if destination is None:
                continue
            if (destination.chain != 'ethereum' or destination.block != block
                    or destination.timestamp != stamp or stamp <= source_stamp
                    or destination.minted or len(destination.movements) != 1
                    or destination.movements[0].account != f'ethereum:{ETH_ALM}:{remote}'
                    or destination.movements[0].external_income
                    or abs(destination.movements[0].change - arrival_value) > D('1e-8')):
                raise ValueError('Spark OP/Uni withdrawal receipt differs')
            index[destination_id + suffix] = replace(destination, identity=destination_id + suffix,
                movements=(*destination.movements,
                    AssetMovement(claim, arrival_value, -arrival_value, preserve_basis=True)))
            del index[destination_id]
        del index[source_id]
        changed = True
    if not changed:
        return history
    return replace(history, batches=tuple(index.values()), venue_accounts=mapping,
                   analytics_only_venues=tuple(sorted(extras)))
