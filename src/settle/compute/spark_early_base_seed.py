"""Three early spell draws sent through the subproxy to Base (198m USDS).

Exact spells; their draw/wrap/bridge sections identify the route and paid basis:
https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20241114/SparkEthereum_20241114.sol
https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20241128/SparkEthereum_20241128.sol
https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20250109/SparkEthereum_20250109.sol

Canonical message hashes authenticate all four token deliveries. Paid principal
is independent of sUSDS value at receipt. Claims receive no idle exemption.
"""
from dataclasses import replace
from decimal import Decimal as D

ILK = '0x414c4c4f4341544f522d535041524b2d41000000000000000000000000000000'
SUFFIX = ':spark-early-base'
HOLDER = '0x2917956eff0b5eaf030abdb4ef4296df775009ca'
# source tx, block, timestamp, draw; (destination tx, block, time, token, cost, value)
ROUTES = (
    ('0x789c9271fdb91b6afcc48386ea7bd15bb2928ddb347817a922988160380c72be', 21215063, 1731938423, D('9000000'), (
        ('0xef8ecba5cc0eb6a120a2e5ebf40d8a2b29dfc45e5832e4562badd58e9fa9cb11', 22574629, 1731938605,
         '0x820c137fa70c8691f0e44dc420a5e53c168921dc', D('1000000'), D('1000000.00')),
        ('0x2eed3e625f1747f2da728c980abc637844f0c30dacfb6eb90c0c5c0f7093c214', 22574629, 1731938605,
         '0x5875eee11cf8398102fdad704c9e96607675467a', D('8000000'), D('8000003.725114428575064445308')),
    )),
    ('0x1dd63197c18cadffce8107c4d7219e4d6e9afaf75540d07e2e7a60cfb63b8433', 21301124, 1732977323, D('90000000'), (
        ('0x299ca75411f67bec1ce77569affe0fbf2cdc7f7cc542e7a66ab5df3617893cf5', 23094080, 1732977507,
         '0x5875eee11cf8398102fdad704c9e96607675467a', D('90000000'), D('90000046.62040416472337307511')),
    )),
    ('0xdfdb92656de7a33727914e3013c214287ed0c59a02ce6f6c5e3c63fcd0fb8650', 21616020, 1736776823, D('99000000'), (
        ('0x0bc2921ce64356e2127c649c7cba9a3be65053b4d2b0f3e3b98fe962180a0ec1', 24993830, 1736777007,
         '0x820c137fa70c8691f0e44dc420a5e53c168921dc', D('99000000'), D('99000000.00')),
    )),
)


def link_spark_early_base_seed(history):
    from ..normalize.allocation_capital import AssetMovement

    index = {b.identity: b for b in history.batches}
    if len(index) != len(history.batches):
        raise ValueError('Duplicate capital transaction')
    route_ids = {'ethereum:' + tx for tx, *_ in ROUTES}
    route_ids.update('base:' + leg[0] for *_, legs in ROUTES for leg in legs)
    if not any(key in index or key + SUFFIX in index for key in route_ids):
        return history
    mapping = dict(history.venue_accounts)
    extras = set(history.analytics_only_venues)
    for source_tx, block, stamp, draw, legs in ROUTES:
        source_id = 'ethereum:' + source_tx
        identities = [source_id, *('base:' + leg[0] for leg in legs)]
        if any(i + SUFFIX in index for i in identities):
            if any(i in index for i in identities):
                raise ValueError('Cannot append raw events to linked Spark Base seed')
            continue
        source = index.get(source_id)
        if source is None:
            continue  # An unfunded destination remains an unknown receipt.
        if (source.chain != 'ethereum' or source.block != block or source.timestamp != stamp
                or abs(source.minted - draw) > D('1e-8') or source.movements
                or set(source.minted_by_ilk) != {ILK}
                or source.minted_by_ilk[ILK] != source.minted
                or sum((leg[4] for leg in legs), D(0)) != draw):
            raise ValueError('Spark Base seed draw or existing custody differs')
        movements = []
        for tx, dest_block, dest_stamp, token, cost, value in legs:
            account = f'base:{HOLDER}:{token}'
            claim = 'native-bridge:spark:early-base:' + tx
            venue = 'S_BASE_SEED_PENDING_' + str(dest_block) + '_' + token[2:10]
            if venue in mapping and mapping[venue] != claim:
                raise ValueError('Conflicting Spark Base seed claim ownership')
            mapping[venue] = claim
            extras.add(venue)
            movements.append(AssetMovement(claim, D(0), cost))
            destination_id = 'base:' + tx
            destination = index.get(destination_id)
            if destination is None:
                continue
            if (destination.chain != 'base' or destination.block != dest_block
                    or destination.timestamp != dest_stamp or dest_stamp <= stamp
                    or destination.minted or len(destination.movements) != 1
                    or destination.movements[0].account != account
                    or destination.movements[0].external_income
                    or abs(destination.movements[0].change - value) > D('1e-8')):
                raise ValueError('Spark Base seed receipt differs')
            index[destination_id + SUFFIX] = replace(destination, identity=destination_id + SUFFIX,
                movements=(*destination.movements, AssetMovement(claim, value, -value, preserve_basis=True)))
            del index[destination_id]
        index[source_id + SUFFIX] = replace(source, identity=source_id + SUFFIX, movements=tuple(movements))
        del index[source_id]
    return replace(history, batches=tuple(index.values()), venue_accounts=mapping,
                   analytics_only_venues=tuple(sorted(extras)))
