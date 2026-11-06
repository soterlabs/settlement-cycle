"""Carry authenticated June/July 2026 Base withdrawals through native custody.

The July 2 Base spell, executed July 6, burns the ALM's entire USDS/sUSDS
holdings for Ethereum delivery (completed July 13):
https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20260702/SparkBase_20260702.sol

Canonical MessagePassed payloads, their successful L1 RelayedMessage hashes,
portal finalizations and actual token burn/payment legs are pinned in the
regression fixture. This is an exact historical adapter, not amount matching.
In-flight claims are separate financing rows: they are not ALM idle USDS.
"""
from dataclasses import replace
from decimal import Decimal as D

SOURCE = 'base:0x7b8e692e523eca62aaad95e04e279ad7d8c31b38c0694af18f1a0635f54323ae'
BASE_ALM = '0x2917956eff0b5eaf030abdb4ef4296df775009ca'
ETH_ALM = '0x1601843c5e9bc251a3272907010afa41fa18347e'
SUFFIX = ':spark-base-native'
# local token, remote token, actual raw units, L1 receipt, source/arrival USD
LEGS = (
    ('0x820c137fa70c8691f0e44dc420a5e53c168921dc', '0xdc035d45d973e3ec169d2276ddab16f1e407384f',
     146550618210418117475041461, '0xac90aa7e2dc4035c921324e3a10ad3ba6c39f21826375b1558ee6acc27964a22',
     D('146550618.210418117475041461'), D('146550618.210418117475041461'), 25526069, 1783974155),
    ('0x5875eee11cf8398102fdad704c9e96607675467a', '0xa3931d71877c0e7a3148cb7eb4463524fec27fbd',
     191958411108646346259425604, '0xb2c9215da24ae539d7a3fe9b65b731d079f0aaf0229e51e885e35d319e05c9e8',
     D('211563874.6433531834916461955'), D('211709381.797117976940288258'), 25526073, 1783974203),
)


JUNE_SOURCE = 'base:0x133b60b42bdf84271efc2d743808e66c0b4796eadd19ab2e34de9da9da910e64'
# June 22 withdrawal preceding the July spell: canonical MessagePassed payloads are
# frozen in spark_base_june_withdrawals.json. Only sUSDS has a successful relay
# through the August 31 pin; the separate 10,000 USDS claim remains in flight.
JUNE_LEGS = (
    (LEGS[0][0], LEGS[0][1], 10_000 * 10**18, None,
     D('10000'), D('10000'), None, None),
    (LEGS[1][0], LEGS[1][1], 10_000 * 10**18,
     '0x67a82b0fafa4e6ccd659b10c90feba4060a1611cd5c6f5a20e2d5edf9a2bdb1c',
     D('11006.22276111643838'), D('11013.77116017150251'), 25424363, 1782748655),
)


def link_spark_base_withdrawals(history):
    history = _link_group(history, SOURCE, 48285903, 1783361153, LEGS, 'S_BASE_NATIVE_PENDING')
    return _link_group(history, JUNE_SOURCE, 47673994, 1782137335, JUNE_LEGS,
                       'S_BASE_JUNE_NATIVE_PENDING')


def _link_group(history, source_id, source_block, source_stamp, group_legs, venue_prefix):
    from ..normalize.allocation_capital import AssetMovement

    by_id = {b.identity: b for b in history.batches}
    raw_ids = [source_id, *('ethereum:' + leg[3] for leg in group_legs if leg[3])]
    transformed = [source_id + SUFFIX + f':{n+1}' for n in range(len(group_legs))]
    transformed.extend('ethereum:' + leg[3] + SUFFIX for leg in group_legs if leg[3])
    if any(identity in by_id for identity in transformed):
        if any(identity in by_id for identity in raw_ids):
            raise ValueError('Cannot append raw events to linked Spark Base withdrawal')
        return history
    if source_id not in by_id:
        return history  # No opening funded custody may be fabricated.
    source = by_id[source_id]
    if (source.chain != 'base' or source.block != source_block or source.timestamp != source_stamp
            or source.minted or source.minted_by_ilk or len(source.movements) != 2):
        raise ValueError('Spark Base withdrawal source metadata differs')
    movements = source.movements
    mapping = dict(history.venue_accounts)
    extras = set(history.analytics_only_venues)
    replacements = {}
    source_legs = []
    for n, (local, remote, _, tx, source_value, arrival_value, block, stamp) in enumerate(group_legs):
        account = f'base:{BASE_ALM}:{local}'
        legs = [m for m in movements if m.account == account]
        if len(legs) != 1 or legs[0].external_income or abs(legs[0].change+source_value) > D('1e-8'):
            raise ValueError('Spark Base withdrawal burn differs')
        claim = f'native-bridge:spark:base:{tx or source_id + ":" + str(n+1)}'
        venue = f'{venue_prefix}_{n+1}'
        if venue in mapping and mapping[venue] != claim:
            raise ValueError('Conflicting Spark bridge ownership')
        mapping[venue] = claim
        extras.add(venue)
        # Preserve each physical token's funding, rather than pooling USDS
        # and sUSDS in one transaction clearing account before the bridge.
        source_legs.append(replace(source, identity=source_id + SUFFIX + f':{n+1}',
            log_index=source.log_index + n, movements=(replace(legs[0], preserve_basis=True),
                AssetMovement(claim, D(0), source_value))))
        destination = by_id.get('ethereum:'+tx) if tx else None
        if destination is None:
            continue  # Source-only cutoff retains funded custody.
        if (destination.chain != 'ethereum' or destination.block != block
                or destination.timestamp != stamp or destination.minted or destination.minted_by_ilk):
            raise ValueError('Spark Base withdrawal receipt metadata differs')
        received = [m for m in destination.movements if m.account == f'ethereum:{ETH_ALM}:{remote}']
        if len(received) != 1 or received[0].external_income or abs(received[0].change-arrival_value) > D('1e-8'):
            raise ValueError('Spark Base withdrawal receipt differs')
        # Mark the SAME bridged units at delivery. sUSDS appreciation changes
        # value, never borrowed basis; the complete funded claim then releases.
        replacements[destination.identity] = replace(destination, identity=destination.identity+SUFFIX,
            movements=(*destination.movements, AssetMovement(claim, arrival_value, -arrival_value, preserve_basis=True)))
    batches = tuple(replacements.get(b.identity, b) for b in history.batches if b.identity != source_id)
    return replace(history, batches=(*batches, *source_legs),
                   venue_accounts=mapping, analytics_only_venues=tuple(sorted(extras)))
