"""Preserve borrowed principal while sUSDe withdrawals await cooldown.

The ALM owns a claim on USDe in Ethena's silo after its sUSDe shares burn.
That claim is not a new investment or income when unstake releases it.
Source: https://github.com/ethena-labs/bbp-public-assets/blob/f3e56d5f06bfef82367d5d5b561398e91d5bebc1/contracts/contracts/StakedUSDeV2.sol
Spark strategy updates explain the August 5/12, 2025 cooldown and redeployment:
https://forum.skyeco.com/t/spark-liquidity-layer-configuration-and-strategy/25860/140
https://forum.skyeco.com/t/spark-liquidity-layer-configuration-and-strategy/25860/144
"""
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal

from ..domain.primes import Address, Chain
from ..extract import hypersync, hypersync_store, rpc
from ..extract._keccak import keccak256
from ..extract.transfer_logs import TRANSFER_TOPIC0
from .allocation_bridges import _view

SUSDE = '0x9d39a5de30e57443bff2a8307a4256c8797a3497'
USDE = '0x4c9edd5852cd905f086c759e8383e09bff1e68b3'
WITHDRAW = '0x' + keccak256(b'Withdraw(address,address,address,uint256,uint256)').hex()


def _pending_claim(holder, block):
    data = '0x' + keccak256(b'cooldowns(address)')[:4].hex() + holder[2:].rjust(64, '0')
    raw = rpc.eth_call(Chain.ETHEREUM, Address.from_str(SUSDE), data, block)
    if len(raw) != 130:
        raise ValueError('Invalid Ethena cooldown state')
    return int(raw[66:], 16)


def link_ethena_cooldowns(prime, pins, batches, custody):
    from .allocation_capital import AssetMovement

    venues = [v for v in prime.venues if not v.skip and v.chain == Chain.ETHEREUM
              and v.token.address.hex == SUSDE]
    if not venues:
        return batches, custody
    by_id = {b.identity: b for b in batches}
    custody = {v: list(accounts) for v, accounts in custody.items()}
    for venue in venues:
        holder = (venue.holder_override or prime.alm[Chain.ETHEREUM]).hex
        who = '0x' + holder[2:].rjust(64, '0')
        rows = hypersync_store.fetch_logs('ethereum', [
            {'address': [SUSDE], 'topics': [[WITHDRAW], [], [], [who]]},
            {'address': [USDE], 'topics': [[TRANSFER_TOPIC0], [], [who]]},
        ], 0, pins[Chain.ETHEREUM], log_fields=[*hypersync._DEFAULT_LOG_FIELDS, 'transaction_hash'])
        grouped = defaultdict(list)
        for row in rows:
            grouped[(row.block_number, row.transaction_hash)].append(row)
        pending = defaultdict(int)
        # The silo is immutable in StakedUSDeV2. Read historical metadata at
        # each withdrawal block rather than assuming today's configuration.
        silos = set()
        ordered = sorted(grouped.items())
        checked_block = None
        for (block, tx), logs in ordered:
            if checked_block is not None and checked_block != block:
                if sum(pending.values()) != _pending_claim(holder, checked_block):
                    raise ValueError('Ethena cooldown state does not match owned claim')
                checked_block = None
            additions = []
            for row in sorted(logs, key=lambda r: r.log_index):
                if row.topic0 == WITHDRAW:
                    silo = '0x' + _view(Chain.ETHEREUM, SUSDE, 'silo()', block)[-40:]
                    silos.add(silo)
                    if row.topic2 != '0x' + silo[2:].rjust(64, '0'):
                        continue  # Immediate withdrawal directly to a recipient.
                    if len(row.data) != 130:
                        raise ValueError('Invalid Ethena cooldown withdrawal')
                    raw_amount = int(row.data[2:66], 16)
                    change = raw_amount
                elif row.topic1 and '0x' + row.topic1[-40:] in silos:
                    silo = '0x' + row.topic1[-40:]
                    raw_amount = int(row.data, 16)
                    if raw_amount > pending[silo]:
                        raise ValueError('Ethena release exceeds owned cooldown claim')
                    change = -raw_amount
                else:
                    continue
                account = f'ethena-cooldown:{holder}:{silo}'
                additions.append(AssetMovement(account, Decimal(pending[silo]) / Decimal(10**18),
                                                Decimal(change) / Decimal(10**18), preserve_basis=True))
                pending[silo] += change
                if account not in custody.setdefault(venue.id, []):
                    custody[venue.id].append(account)
            if additions:
                checked_block = block
                identity = f'ethereum:{tx}'
                if identity not in by_id:
                    raise ValueError('Ethena cooldown lacks normalized transaction')
                batch = by_id[identity]
                # Combine repeated operations against the same silo within a
                # transaction before replay applies its net asset movements.
                combined = {}
                for m in additions:
                    if m.account in combined:
                        m = replace(combined[m.account], change=combined[m.account].change + m.change)
                    combined[m.account] = m
                by_id[identity] = replace(batch, movements=(*batch.movements, *combined.values()))
        if checked_block is not None and sum(pending.values()) != _pending_claim(holder, checked_block):
            raise ValueError('Ethena cooldown state does not match owned claim')
        if silos and sum(pending.values()) != _pending_claim(holder, pins[Chain.ETHEREUM]):
            raise ValueError('Ethena pinned cooldown state does not match owned claim')
    return list(by_id.values()), custody
