"""NFT liquidity funding, including principal awaiting V3 Collect.

An NFT is a custody wrapper. Liquidity additions carry the funding of paid
assets; fee collections are own money, not released borrowed principal.
"""
from collections import defaultdict
from decimal import Decimal

from ..domain.primes import Address
from ..domain.sky_tokens import PAR_STABLES_BY_CHAIN
from ..extract import hypersync, hypersync_store
from ..extract import uniswap_v3 as v3
from ..extract import uniswap_v4 as v4
from ..extract.transfer_logs import TRANSFER_TOPIC0

ZERO = Decimal(0)


def _topic(address):
    return '0x' + address.value.hex().rjust(64, '0')


def _words(row):
    return [int(row.data[i:i + 64], 16) for i in range(2, len(row.data), 64)]


class NFTCapital:
    def __init__(self, prime, chain):
        self.chain = chain
        self.venues = [v for v in prime.venues if v.chain == chain and not v.skip
                       and v.lp_kind in ('uniswap_v3', 'uniswap_v4')]
        self.holders = {v.id: v.holder_override or prime.alm[chain] for v in self.venues}
        self.positions = {}
        self.liquidity = defaultdict(int)
        self.owed = defaultdict(lambda: [0, 0])
        self.custody_accounts = defaultdict(list)
        self.accounts = {v.id: f'nft:{chain.value}:{v.id}' for v in self.venues}

    def _usd(self, coins, amounts):
        result = ZERO
        for coin, amount in zip(coins, amounts, strict=True):
            if not amount:
                continue
            entry = PAR_STABLES_BY_CHAIN.get(self.chain, {}).get(coin.value)
            if entry is None:
                raise ValueError(f'NFT funding needs historical pricing for {coin.hex}')
            result += Decimal(amount) / Decimal(10**entry[1])
        return result

    def discover(self, logs, pin):
        """Discover V3 ids from all historical NFT transfers, not end snapshots."""
        selections = []
        for venue in self.venues:
            manager = venue.nft_position_manager or v3.NFPM_CANONICAL
            if venue.lp_kind == 'uniswap_v4':
                key = v4.V4PoolKey(**{k: getattr(venue.univ4_pool_key, k)
                                     for k in ('currency0', 'currency1', 'fee', 'tick_spacing', 'hooks')})
                for tid in venue.univ4_token_ids:
                    self.positions[(manager.hex, tid, venue.id)] = (venue, key)
                selections.append({'address': [v4.POOL_MANAGER_CANONICAL.hex], 'topics': [
                    [v4.TOPIC_MODIFY_LIQUIDITY], ['0x' + key.pool_id().hex()], [_topic(manager)]]})
                continue
            ids = {}
            holder = _topic(self.holders[venue.id])
            for row in logs:
                if (row.address == manager.hex and row.topic0 == TRANSFER_TOPIC0 and row.topic3
                        and holder in (row.topic1, row.topic2)):
                    ids.setdefault(int(row.topic3, 16), row.block_number)
            matched = []
            for tid, first in ids.items():
                position = v3.read_position(self.chain, manager, tid, first)
                pool = v3.read_pool_state(self.chain, venue.token.address, first)
                if (position.token0, position.token1, position.fee) != (pool.token0, pool.token1, pool.fee):
                    continue
                self.positions[(manager.hex, tid, venue.id)] = (venue, position)
                matched.append('0x' + f'{tid:064x}')
            if matched:
                selections.append({'address': [manager.hex], 'topics': [[
                    v3.TOPIC_INCREASE_LIQUIDITY, v3.TOPIC_DECREASE_LIQUIDITY, v3.TOPIC_COLLECT,
                ], matched]})
        if not selections:
            return []
        return hypersync_store.fetch_logs(self.chain.value, selections, 0, pin,
            log_fields=[*hypersync._DEFAULT_LOG_FIELDS, 'transaction_hash'])

    def movements(self, logs):
        from .allocation_capital import AssetMovement

        movements, fees = [], defaultdict(Decimal)
        for row in sorted(logs, key=lambda r: r.log_index):
            transferred = None
            if row.topic0 == TRANSFER_TOPIC0 and row.topic3:
                tid = int(row.topic3, 16)
                if int(row.topic1, 16) and int(row.topic2, 16):
                    for (manager, ident, vid), (venue, pos) in self.positions.items():
                        if manager != row.address or ident != tid or venue.lp_kind != 'uniswap_v3':
                            continue
                        who = _topic(self.holders[vid])
                        if who not in (row.topic1, row.topic2):
                            continue
                        if transferred is None:
                            snapshot = v3.read_position(self.chain, Address.from_str(manager), tid,
                                                        row.block_number - 1)
                            pool = v3.read_pool_state(self.chain, venue.token.address, row.block_number)
                            amounts = v3.get_amounts_for_liquidity(pool.sqrt_price_x96,
                                v3.get_sqrt_ratio_at_tick(pos.tick_lower),
                                v3.get_sqrt_ratio_at_tick(pos.tick_upper), snapshot.liquidity)
                            previous = next((other_vid for (mgr, ident2, other_vid) in self.positions
                                             if mgr == manager and ident2 == tid
                                             and _topic(self.holders[other_vid]) == row.topic1), None)
                            owed = list(self.owed[f'nft:{self.chain.value}:{manager}:{tid}:{previous}']) if previous else [0, 0]
                            transferred = snapshot.liquidity, self._usd((pos.token0, pos.token1), amounts), owed
                        liquidity, value, owed = transferred
                        account = f'nft:{self.chain.value}:{manager}:{tid}:{vid}'
                        if account not in self.custody_accounts[vid]:
                            self.custody_accounts[vid].append(account)
                        incoming = who == row.topic2
                        movements.append(AssetMovement(account, ZERO if incoming else value,
                                                       value if incoming else -value, preserve_basis=True))
                        queue = account + ':collect'
                        queued_value = self._usd((pos.token0, pos.token1), owed)
                        if queued_value:
                            movements.append(AssetMovement(queue, ZERO if incoming else queued_value,
                                queued_value if incoming else -queued_value, preserve_basis=True))
                            if queue not in self.custody_accounts[vid]:
                                self.custody_accounts[vid].append(queue)
                        self.owed[account] = list(owed) if incoming else [0, 0]
                        self.liquidity[account] = liquidity if incoming else 0
            for (manager, tid, vid), (venue, position) in self.positions.items():
                holder = self.holders[vid]
                if venue.lp_kind == 'uniswap_v4':
                    if (row.address != v4.POOL_MANAGER_CANONICAL.hex or row.topic0 != v4.TOPIC_MODIFY_LIQUIDITY
                            or row.topic1 != '0x' + position.pool_id().hex()
                            or row.topic2 != _topic(Address.from_str(manager))):
                        continue
                    words = _words(row)
                    if len(words) != 4 or words[3] != tid:
                        continue
                    # A configured id is not proof of continuous ownership.
                    owner = v4.owner_of(self.chain, Address.from_str(manager), tid, row.block_number)
                    if owner != holder:
                        owner = v4.owner_of(self.chain, Address.from_str(manager), tid, row.block_number - 1)
                        if owner != holder:
                            continue
                    delta = words[2] - (1 << 256) if words[2] >= 1 << 255 else words[2]
                    lower = v4._decode_int24(f'{words[0]:064x}')
                    upper = v4._decode_int24(f'{words[1]:064x}')
                    slot = v4.read_slot0(self.chain, v4.POOL_MANAGER_CANONICAL, position.pool_id(), row.block_number)
                    amounts = v3.get_amounts_for_liquidity(slot.sqrt_price_x96,
                        v3.get_sqrt_ratio_at_tick(lower), v3.get_sqrt_ratio_at_tick(upper), abs(delta))
                    coins = (position.currency0, position.currency1)
                    # V4 fees are settled with liquidity changes. Until their
                    # exact amounts are decoded, differences remain evidence
                    # in the shared transaction replay, never new borrowing.
                else:
                    if row.address != manager or row.topic1 != '0x' + f'{tid:064x}':
                        continue
                    if row.topic0 not in (v3.TOPIC_INCREASE_LIQUIDITY, v3.TOPIC_DECREASE_LIQUIDITY, v3.TOPIC_COLLECT):
                        continue
                    # Track ownership at execution, including burn transactions.
                    owners = [r for r in logs if r.address == manager and r.topic0 == TRANSFER_TOPIC0
                              and r.topic3 == '0x' + f'{tid:064x}']
                    if owners:
                        if not any(_topic(holder) in (r.topic1, r.topic2) for r in owners):
                            continue
                    else:
                        from ..extract import rpc
                        raw = rpc.eth_call(self.chain, Address.from_str(manager),
                                           '0x6352211e' + f'{tid:064x}', row.block_number)
                        if raw[-40:] != holder.hex[2:]:
                            continue
                    coins = (position.token0, position.token1)
                    words = _words(row)
                    if len(words) != 3:
                        raise ValueError('Invalid V3 liquidity/collect event')
                    amounts = words[1:]
                    delta = words[0] if row.topic0 == v3.TOPIC_INCREASE_LIQUIDITY else -words[0]
                account = f'nft:{self.chain.value}:{manager}:{tid}:{vid}'
                queue = account + ':collect'
                for a in (account, queue):
                    if a not in self.custody_accounts[vid]:
                        self.custody_accounts[vid].append(a)
                old_liquidity = self.liquidity[account]
                if venue.lp_kind == 'uniswap_v3' and row.topic0 == v3.TOPIC_COLLECT:
                    owed = self.owed[account]
                    released = [min(a, o) for a, o in zip(amounts, owed, strict=True)]
                    movements.append(AssetMovement(queue, self._usd(coins, owed), -self._usd(coins, released)))
                    recipient = '0x' + f'{words[0]:064x}'[-40:]
                    for i, coin in enumerate(coins):
                        fees[(coin.hex, recipient)] += self._usd((coin,), (amounts[i] - released[i],))
                        owed[i] -= released[i]
                    continue
                if not delta:
                    continue
                if old_liquidity + delta < 0:
                    raise ValueError(f'NFT liquidity history is incomplete: {account}')
                value = self._usd(coins, amounts)
                before = value * Decimal(old_liquidity) / Decimal(abs(delta))
                movements.append(AssetMovement(account, before, value if delta > 0 else -value))
                self.liquidity[account] += delta
                if delta < 0 and venue.lp_kind == 'uniswap_v3':
                    owed = self.owed[account]
                    movements.append(AssetMovement(queue, self._usd(coins, owed), value))
                    for i, amount in enumerate(amounts):
                        owed[i] += amount
        return movements, fees
