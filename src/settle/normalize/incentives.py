"""Pinned issuer-incentive inputs. Providers are injectable for offline replay."""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from ..compute.incentives import EligibleBalance, daily_accrual, reconcile
from ..domain.monthly_pnl import VenueRevenue
from ..domain.primes import Address, Chain
from ..extract import rpc
from ..extract._keccak import keccak256
from ..extract.fred import fed_lower
from ..normalize.registry import get_balance_source, get_block_resolver

ZERO = Decimal(0)


def _call(address: Address, signature: str, block: int, args: str = '') -> list[int]:
    selector = keccak256(signature.encode()).hex()[:8]
    raw = rpc.eth_call(Chain.ETHEREUM, address, '0x' + selector + args, block)
    hx = raw.removeprefix('0x')
    if not hx or len(hx) % 64:
        raise ValueError(f'Malformed {signature} response at block {block}')
    return [int(hx[i:i+64], 16) for i in range(0, len(hx), 64)]


def _address(word: int) -> Address:
    if not 0 < word < 2**160:
        raise ValueError('Invalid contract address')
    return Address(word.to_bytes(20, 'big'))


class IncentiveInputs:
    def __init__(self, prime, sources, period=None):
        self.prime, self.sources, self.period = prime, sources, period
        self.resolver = sources.block_resolver or get_block_resolver()
        self._nfts = {}

    def block(self, chain: Chain, day: date) -> int:
        if self.period is not None and day == self.period.end:
            return self.period.pin_blocks[chain]
        block = self.resolver.block_at_or_before(chain.value, datetime.combine(day, time.max, UTC))
        if self.period is not None and (day > self.period.end or block > self.period.pin_blocks[chain]):
            raise ValueError('Incentive input exceeds pinned cutoff')
        return block

    def eligible(self, venue, day: date) -> list[EligibleBalance]:
        cfg = venue.incentive
        block = self.block(venue.chain, day)
        out = []
        by_id = {v.id: v for v in self.prime.venues}
        for component in cfg['components']:
            if component.get('through') and day > date.fromisoformat(component['through']):
                continue
            kind = component['kind']
            source = by_id.get(component.get('venue'))
            holder = self.prime.alm[venue.chain]
            token = venue.token.address
            scale = Decimal(10 ** venue.token.decimals)
            name = component.get('venue', kind)
            if kind == 'raw':
                value = Decimal(rpc.balance_of(venue.chain, token, holder, block)) / scale
                out.append(EligibleBalance(name, value))
            elif kind == 'lending_idle':
                pool = Address.from_str(component['pool'])
                reserve = _call(pool, 'getReserveData(address)', block, token.value.hex().zfill(64))
                if len(reserve) < 11:
                    raise ValueError('Incomplete SparkLend reserve data')
                if reserve[8] == 0:  # reserve has not been listed yet
                    out.append(EligibleBalance(name, ZERO))
                    continue
                atoken, stable_debt, variable_debt = map(_address, reserve[8:11])
                supplied = Decimal(rpc.balance_of(venue.chain, atoken, holder, block)) / scale
                if supplied == 0:
                    out.append(EligibleBalance(name, ZERO))
                    continue
                idle = rpc.balance_of(venue.chain, token, atoken, block)
                debt = (rpc.total_supply_of(venue.chain, stable_debt, block)
                        + rpc.total_supply_of(venue.chain, variable_debt, block))
                value = supplied * Decimal(idle) / Decimal(idle + debt) if idle + debt else ZERO
                out.append(EligibleBalance(name, value))
            elif kind == 'uniswap_v4':
                from .positions import _venue_v4_pool_key
                from .sources.uniswap_v4 import RPCUniswapV4PositionSource
                key = _venue_v4_pool_key(source)
                if '0x' + key.pool_id().hex() != component['pool_id'].lower():
                    raise ValueError('Issuer incentive pool ID does not match configured PoolKey')
                provider = self.sources.v4_position or RPCUniswapV4PositionSource()
                ids = self.owned_v4_ids(source, holder, block)
                if not ids:
                    out.append(EligibleBalance(name, ZERO))
                    continue
                positions = provider.positions_in_pool(venue.chain.value, holder.value, ids, key, block)
                amount = sum((p.amount0 if p.currency0 == token else
                              p.amount1 if p.currency1 == token else 0) for p in positions)
                out.append(EligibleBalance(name, Decimal(amount) / scale))
            elif kind == 'curve':
                from .sources.curve_pool import CurvePoolSource
                provider = self.sources.curve_pool or CurvePoolSource()
                shares = rpc.balance_of(venue.chain, source.token.address, holder, block)
                if shares == 0:
                    out.append(EligibleBalance(name, ZERO))
                    continue
                state = provider.read_pool(venue.chain.value, source.token.address.value, block)
                if token not in state.coins:
                    raise ValueError('Issuer reward token missing from Curve pool')
                value = (Decimal(shares) / Decimal(state.total_supply)
                         * Decimal(state.balances[state.coins.index(token)]) / scale
                         if state.total_supply else ZERO)
                out.append(EligibleBalance(name, value))
            elif kind == 'morpho_top_up':
                vault = Address.from_str(component['vault'])
                shares = rpc.balance_of(venue.chain, vault, holder, block)
                if not shares:
                    out.append(EligibleBalance(name, ZERO, ZERO))
                    continue
                if component['net_apr_source'] != 'daily_share_return':
                    raise ValueError('Unsupported net vault APR source')
                # previewRedeem includes pending fee dilution. Do NOT subtract
                # performanceFee again: that would charge the fee twice.
                unit = 10**24
                now = _call(vault, 'previewRedeem(uint256)', block, hex(unit)[2:].zfill(64))[0]
                previous = _call(vault, 'previewRedeem(uint256)',
                                 self.block(venue.chain, day - timedelta(days=1)),
                                 hex(unit)[2:].zfill(64))[0]
                if previous <= 0 or now <= 0:
                    raise ValueError('Missing net vault share-price observation')
                net_apr = (Decimal(now) / Decimal(previous) - 1) * 365
                assets = _call(vault, 'previewRedeem(uint256)', block, hex(shares)[2:].zfill(64))[0]
                out.append(EligibleBalance(name, Decimal(assets) / scale, net_apr))
            else:
                raise ValueError(f'Unknown incentive component {kind}')
        return out

    def owned_v4_ids(self, source, holder, block):
        """Discover every open NFT from transfer history, including later mints."""
        from ..extract import hypersync_store
        from ..extract.transfer_logs import TRANSFER_TOPIC0, _to_topic_hex
        pm = source.nft_position_manager
        key = (pm, holder, block)
        if key not in self._nfts:
            topic = _to_topic_hex(holder.value)
            logs = hypersync_store.fetch_logs(source.chain.value, [
                {'address': [pm.hex], 'topics': [[TRANSFER_TOPIC0], [topic]]},
                {'address': [pm.hex], 'topics': [[TRANSFER_TOPIC0], [], [topic]]},
            ], 0, block)
            owned = set()
            for row in sorted({(r.block_number, r.log_index): r for r in logs}.values(),
                              key=lambda r: (r.block_number, r.log_index)):
                if row.topic3 is None or row.topic2 is None:
                    raise ValueError('Malformed NFT Transfer')
                tid = int(row.topic3, 16)
                if row.topic2.lower() == topic.lower():
                    owned.add(tid)
                else:
                    owned.discard(tid)
            self._nfts[key] = sorted(owned)
        return self._nfts[key]

    def receipts(self, venue, start, cutoff):
        cfg = venue.incentive
        provider = self.sources.balance or get_balance_source()
        amounts = {}
        for entry in cfg['receipt_sources']:
            chain = Chain(entry.get('chain', venue.chain.value))
            frame = provider.directed_inflow_timeseries(
                chain=chain.value, token=Address.from_str(entry['token']).value,
                from_addr=Address.from_str(entry['payer']).value,
                to_addr=Address.from_str(entry['recipient']).value,
                start=start, pin_block=self.block(chain, cutoff))
            for _, row in frame.iterrows():
                day = row['block_date']
                if hasattr(day, 'date'):
                    day = day.date()
                if start <= day <= cutoff:
                    amounts[day] = amounts.get(day, ZERO) + Decimal(str(row['daily_inflow']))
        return amounts

    def rates(self, start, cutoff):
        return fed_lower(start, cutoff)


def compute_incentive(prime, venue, period, sources, *, provider=None):
    cfg = venue.incentive
    start = date.fromisoformat(cfg['accrual_start'])
    if period.end < start:
        return None
    provider = provider or IncentiveInputs(prime, sources, period)
    if cfg.get('opening_balance') is None or not cfg.get('receipt_sources'):
        raise ValueError(f'{venue.id}: incentive opening balance and receipt sources are required')
    first = max(period.start, start)
    rates = provider.rates(start, period.end) if cfg['program'] == 'pyusd' else {}
    daily = {}
    day = start
    while day <= period.end and cfg['program'] != 'usdg':
        daily[day] = daily_accrual(cfg['program'], day, provider.eligible(venue, day), rates.get(day))
        day += timedelta(days=1)
    receipts = provider.receipts(venue, start, period.end)
    ledger = reconcile(cfg['program'], start, first, period.end, daily, receipts,
                       Decimal(str(cfg['opening_balance'])))
    ledger['cutoff'] = str(period.end)
    ledger['period_start'] = str(first)
    ledger['receipt_sources'] = cfg['receipt_sources']
    if rates:
        ledger['reference_rates'] = {'series': 'DFEDTARL',
            'source': 'https://fred.stlouisfed.org/series/DFEDTARL',
            'observations': {str(day): str(rate) for day, rate in rates.items()}}
    return VenueRevenue(
        venue_id=venue.id, label=venue.label, settlement_enabled=False,
        pricing_category=venue.pricing_category.value,
        value_som=Decimal(ledger['period_opening_balance']),
        value_eom=Decimal(ledger['accrued_balance']),
        period_inflow=-Decimal(ledger['period_receipts']),
        revenue=Decimal(ledger['period_accrual']), actual_revenue=Decimal(ledger['period_accrual']),
        cof_excluded=True, incentive_accrual=ledger)
