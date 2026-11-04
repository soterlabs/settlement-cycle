"""Pinned reporting inputs; never an input to MSC charges.

Curator accrual = fee shares received at event NAV + closing unminted fee
entitlement - opening entitlement. Net asset price revaluation uses average
daily EOD underlying exposure and is explicitly an estimate, not FIFO P&L.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, localcontext
from functools import cache
from itertools import pairwise
from time import sleep

import requests

from ..domain.primes import Address, Chain
from ..extract import hypersync
from ..extract._keccak import keccak256
from ..extract.cache import cached
from ..extract.hypersync import _DEFAULT_LOG_FIELDS
from ..extract.hypersync_store import _reorg_margin, fetch_logs
from ..extract.rpc import balance_of, eth_call, native_balance
from ..extract.transfer_logs import TRANSFER_TOPIC0
from ..normalize.allocation_morpho_fees import (
    ACCRUE_INTEREST,
    ACCRUE_INTEREST_V2,
    ZERO_TOPIC,
    fee_mints,
)
from .allocation_revenue import SPARK_EXTRA
from .financial_allocations import ALLOCATIONS
from .verification import digest

D = Decimal
WAD = 10**18
ETH_ALM = '0x1601843c5e9bc251a3272907010afa41fa18347e'
BASE_ALM = '0x2917956eff0b5eaf030abdb4ef4296df775009ca'
TREASURIES = ('0xb137e7d16564c81ae2b0c8ee6b55de81dd46ece5', '0x856900aa78e856a5df1a2665ee3a66b2487cd68f')
CURATORS = (
    ('S10', 'ethereum', '0x56a76b428244a50513ec81e225a293d128fd581d', 6, False),
    ('S11', 'ethereum', '0xc7cdcfdefc64631ed6799c95e3b110cd42f2bd22', 6, True),
    ('S65', 'ethereum', '0xb0c424116172b55cbb6dd3136f5989f7959e5b91', 6, True),
    ('S12', 'ethereum', '0x73e65dbd630f90604062f6e02fab9138e713edd9', 18, False),
    ('S13', 'ethereum', '0xe41a0583334f0dc4e023acd0bfef3667f6fe0597', 18, False),
    ('S34', 'base', '0x7bfa7c4f149e7415b73bdedfe609237e29cbf34a', 6, False),
)
STABLE_RESERVES = {
    '0xc02ab1a5eaa8d1b114ef786d9bde108cd4364359': ('usds', 18),
    '0x377c3bd93f2a2984e1e7be6a5c22c525ed4a4815': ('usdc', 6),
    '0xe7df13b8e3d6740fe17cbe928c7334243d86c92f': ('usdt', 6),
    '0x4dedf26112b3ec8ec46e7e31ea5e123490b05b8b': ('dai', 18),
    '0x779224df1c756b4edd899854f32a53e8c2b2ce5d': ('pyusd', 6),
    '0x59275fb72c8004f44ba44432e25082932fd677f1': ('rlusd', 18),
    '0x6f335538257ef440f3c51e925a5c820f722a1f9f': ('usdg', 6),
}
MORPHO = {'ethereum': '0x58d97b57bb95320f9a05dc918aef65434969c2b2',
          'base': '0xbaa5cc21fd487b8fcc2f632f3f4e8d37262a0842'}
WETH = {'ethereum': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
        'base': '0x4200000000000000000000000000000000000006',
        'arbitrum': '0x82af49447d8a07e3bd95bd0d56f35241523fbab1',
        'optimism': '0x4200000000000000000000000000000000000006',
        'unichain': '0x4200000000000000000000000000000000000006'}
SPWETH = '0x59cd1c87501baa753d0b5b5ab5d8416a45cd71db'
SPETH = '0xfe6eb3b609a7c8352a241f7f3a21cea4e9209b8f'
WAVAX = '0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7'
OTHER_NONSTABLE_RESERVES = (
    '0x12b54025c112aa61face2cdb7118740875a566e9', '0x4197ba364ae6698015ae5c1468f54087602715b2',
    '0x7b481acc9fdaddc9af2cbea1ff2342cb1733e50f', '0x9985df20d7e9103ecbceb16a84956434b6f06ae8',
    '0x3cfd5c0d4acaa8faee335842e4f31159fc76b008', '0xb3973d459df38ae57797811f2a1fd061da1bc123',
    '0xa9d4ecebd48c282a70cfd3c469d6c8f178a5738e', '0xce6ca9cdce00a2b0c0d1dac93894f4bd2c960567',
    '0xb131cd463d83782d4de33e00e35ef034f0869ba1', '0x856f1ea78361140834fdcd0db0b08079e4a45062',
)


def topic(address):
    return '0x' + address[2:].zfill(64)


@cached(source_id="spark-reporting.market-price.v2")
def market_price(coin, timestamp):
    # Nearest historical quotes can lie after the requested cutoff. Retry
    # earlier anchors; keep strict non-forward, age and confidence checks.
    for offset in (300, 900, 1800, 3600):
        for attempt in range(4):
            response = requests.get(
                f"https://coins.llama.fi/prices/historical/{timestamp - offset}/{coin}", timeout=30
            )
            if response.status_code not in (429, 502, 503, 504) or attempt == 3:
                response.raise_for_status()
                break
            sleep(2**attempt)
        raw = response.json()["coins"][coin]
        price = D(str(raw["price"]))
        if not price.is_finite() or price <= 0 or D(str(raw.get("confidence", 0))) < D("0.9"):
            raise ValueError("Historical price lacks confidence or a positive amount")
        age = timestamp - int(raw["timestamp"])
        if 0 <= age <= 7200:
            return {"price": str(price), "timestamp": int(raw["timestamp"])}
        if age > 7200:
            break
    raise ValueError("Historical price is stale or forward-looking")


@cached(source_id='spark-reporting.certified-boundary.v1')
def certified_boundary(chain, day, margin):
    """Validate the fast legacy candidate before admitting it to a new cache."""
    if margin < 0:
        raise ValueError('Finality margin must be nonnegative')
    target = int(datetime.combine(day, time.max, UTC).timestamp())
    candidate = hypersync.find_block_at_or_before(chain, target)
    if (candidate + 1 <= hypersync.archive_height(chain) - margin
            and hypersync.block_timestamp_uncached(chain, candidate) <= target
            < hypersync.block_timestamp_uncached(chain, candidate + 1)):
        return candidate
    return hypersync.find_finalized_block_at_or_before(chain, target, margin)


@cache
def _reporting_head(chain):
    from ..extract.rpc import _post, rpc_url

    return int(_post(rpc_url(Chain(chain)), "eth_blockNumber", []), 16)



@cached(source_id="spark-reporting.saved-boundary.v1")
def certified_saved_boundary(chain, day, candidate):
    """Verify an immutable daily source's candidate without a genesis search."""
    from ..extract.rpc import _post, rpc_url

    target = int(datetime.combine(day, time.max, UTC).timestamp())

    def stamp(block):
        raw = _post(rpc_url(Chain(chain)), "eth_getBlockByNumber", [hex(block), False])
        if not raw or int(raw["number"], 16) != block:
            raise ValueError("Missing candidate block header")
        return int(raw["timestamp"], 16)

    if candidate + _reorg_margin() > _reporting_head(chain):
        raise ValueError("Saved candidate lacks finality margin")
    if stamp(candidate) <= target < stamp(candidate + 1):
        return candidate
    return certified_boundary(chain, day, _reorg_margin())



class Source:
    def __init__(self, prime, period, opening_pins, *, daily_pins=None):
        self.prime, self.period = prime, period
        self.daily_pins = daily_pins or {}
        self.opening_pins = {str(k): v for k, v in opening_pins.items()}
        self.closing_pins = {str(k): v for k, v in period.pin_blocks.items()}
        self.block = cache(self._block)

    def _block(self, chain, day):
        if day == self.period.start - timedelta(days=1):
            return self.opening_pins[chain]
        if day == self.period.end:
            return self.closing_pins[chain]
        candidate = self.daily_pins.get(str(day), {}).get(chain)
        if candidate is not None:
            return certified_saved_boundary(chain, day, candidate)
        return certified_boundary(chain, day, _reorg_margin())

    def call(self, chain, token, signature, block, *args):
        data = '0x' + keccak256(signature.encode()).hex()[:8] + ''.join(f'{arg:064x}' for arg in args)
        return eth_call(Chain(chain), Address.from_str(token), data, block)

    def number(self, chain, token, signature, block, *args):
        raw = self.call(chain, token, signature, block, *args)
        if raw == '0x':
            raise ValueError('Contract is not deployed at this boundary')
        return int(raw, 16)

    def balance(self, chain, token, day):
        return D(balance_of(Chain(chain), Address.from_str(token), self.prime.alm[Chain(chain)], self.block(chain, day))) / WAD

    def native(self, chain, day):
        return D(native_balance(Chain(chain), self.prime.alm[Chain(chain)], self.block(chain, day))) / WAD

    def logs(self, chain, selections):
        start, end = self.block(chain, self.period.start - timedelta(days=1)), self.block(chain, self.period.end)
        return fetch_logs(chain, selections, start + 1, end,
                          log_fields=[*_DEFAULT_LOG_FIELDS, 'transaction_hash'])

    def price(self, coin, day):
        timestamp = int(datetime.combine(day, time.max, UTC).timestamp())
        return D(market_price(coin, timestamp)['price'])


def pending_fee(source, spec, block):
    _, chain, vault, decimals, v2 = spec
    holder = ETH_ALM if chain == 'ethereum' else BASE_ALM
    # An empty totalAssets result is only accepted if every standard getter
    # also has empty output: these known vaults do not have such a fallback.
    raw = source.call(chain, vault, 'totalAssets()', block)
    if raw == '0x':
        if source.call(chain, vault, 'totalSupply()', block) != '0x':
            raise ValueError('Inconsistent predeployment state')
        return D(0)
    assets = int(raw, 16)
    if v2:
        raw = source.call(chain, vault, 'accrueInterestView()', block)
        if len(raw) != 194:
            raise ValueError('Invalid pending V2 fee result')
        shares = 0
        for index, getter in ((1, 'performanceFeeRecipient()'), (2, 'managementFeeRecipient()')):
            recipient = source.call(chain, vault, getter, block)
            if '0x' + recipient[-40:] == holder:
                shares += int(raw[2 + index*64:2 + (index+1)*64], 16)
    else:
        recipient = source.call(chain, vault, 'feeRecipient()', block)
        if '0x' + recipient[-40:] != holder:
            return D(0)
        supply = source.number(chain, vault, 'totalSupply()', block)
        # Infer the contract's own pending dilution, including its loss rules.
        virtual = 10**(18 - decimals)
        shares = source.number(chain, vault, 'convertToShares(uint256)', block, assets + 1) - supply - virtual
        if shares < 0:
            raise ValueError('Unexpected ERC4626 conversion semantics')
    return D(source.number(chain, vault, 'convertToAssets(uint256)', block, shares)) / 10**decimals if shares else D(0)


def curator_fees(source, spec):
    venue, chain, vault, decimals, v2 = spec
    holder = ETH_ALM if chain == 'ethereum' else BASE_ALM
    rows = source.logs(chain, [
        {'address': [vault], 'topics': [[ACCRUE_INTEREST, ACCRUE_INTEREST_V2]]},
        {'address': [vault], 'topics': [[TRANSFER_TOPIC0], [ZERO_TOPIC]]},
        {'address': [vault], 'topics': [[TRANSFER_TOPIC0], [], [ZERO_TOPIC]]},
    ])
    if any(not row.transaction_hash for row in rows):
        raise ValueError('Fee history is missing transaction identities')
    by_index = {}
    for row in rows:
        key = row.block_number, row.log_index
        if key in by_index and by_index[key] != row:
            raise ValueError('Conflicting global vault supply event')
        by_index[key] = row
    opening_block, closing_block = source.block(chain, source.period.start - timedelta(days=1)), source.block(chain, source.period.end)
    def total_supply(block):
        raw = source.call(chain, vault, 'totalSupply()', block)
        return int(raw, 16) if raw != '0x' else 0
    opening_supply = supply = total_supply(opening_block)
    received, witnesses = D(0), []
    virtual = 10**(18 - decimals)
    for (block, index), row in sorted(by_index.items()):
        if row.topic0 == TRANSFER_TOPIC0:
            if len(row.data) != 66:
                raise ValueError('Invalid global vault supply movement')
            supply += int(row.data, 16) * ((row.topic1 == ZERO_TOPIC) - (row.topic2 == ZERO_TOPIC))
            if supply < 0:
                raise ValueError('Incomplete global vault supply history')
            continue
        if row.topic0 not in (ACCRUE_INTEREST, ACCRUE_INTEREST_V2):
            continue
        offsets = (1, 2) if v2 else (-1,)
        context = [row, *(by_index[(block, index+i)] for i in offsets if (block, index+i) in by_index)]
        quantity = fee_mints(Chain(chain), context, {(vault, holder)}).get((vault, holder), 0)
        if quantity:
            words = [int(row.data[i:i+64], 16) for i in range(2, len(row.data), 64)]
            assets = words[1] if v2 else words[0]
            post_supply = supply + words[2] + words[3] if v2 else supply
            value = quantity * (assets + 1) // (post_supply + virtual)
            received += D(value) / 10**decimals
            witnesses.append((block, row.transaction_hash, index, quantity, assets, post_supply))
    if supply != total_supply(closing_block):
        raise ValueError('Global vault supply history does not reproduce the closing state')
    opening = pending_fee(source, spec, opening_block)
    closing = pending_fee(source, spec, closing_block)
    return {'venue': venue, 'chain': chain, 'vault': vault, 'status': 'available',
            'recipient_filter': holder,
            'received_usd': str(received), 'opening_pending_usd': str(opening), 'closing_pending_usd': str(closing),
            'accrued_usd': str(received + closing - opening), 'receipt_event_digest': digest(witnesses),
            'receipt_events': len(witnesses), 'recognition': 'accrual', 'opening_total_supply': str(opening_supply),
            'closing_total_supply': str(supply),
            'basis': 'authenticated_fee_mints_at_event_NAV_plus_change_in_unminted_entitlement'}


def reserve_fees(source):
    rows = source.logs('ethereum', [{'topics': [[TRANSFER_TOPIC0], [topic(a) for a in TREASURIES], [topic(ETH_ALM)]]}])
    amounts = {symbol: D(0) for symbol, _ in STABLE_RESERVES.values()}
    unknown = set()
    for row in rows:
        if row.address not in STABLE_RESERVES:
            unknown.add(row.address)
            continue
        symbol, decimals = STABLE_RESERVES[row.address]
        amounts[symbol] += D(int(row.data, 16)) / 10**decimals
    return {'amounts': {k: str(v) for k, v in amounts.items()}, 'unknown_tokens': sorted(unknown),
            'event_digest': digest([(r.block_number, r.log_index, r.address, r.data) for r in rows]),
            'status': 'partial' if unknown else 'available', 'recognition': 'receipt_in_kind'}


def revaluation(samples):
    """(gross units, depositor liability units, unit price) at daily boundaries."""
    asset = liability = D(0)
    held = False
    with localcontext() as ctx:
        ctx.prec = 60
        for before, after in pairwise(samples):
            gross0, owed0, price0 = before
            gross1, owed1, price1 = after
            held |= gross0 != owed0 or gross1 != owed1
            asset += (gross0 + gross1) / 2 * (price1 - price0)
            liability += (owed0 + owed1) / 2 * (price1 - price0)
        net = asset - liability
    return {'status': 'available', 'value': str(net),
            'asset_price_gain_usd': str(asset), 'depositor_offset_usd': str(liability),
            'quality': 'estimate' if held else 'supported', 'recognition': 'accrual',
            'basis': 'average_daily_EOD_net_exposure_times_underlying_price_change',
            'opening_units': str(samples[0][0] - samples[0][1]),
            'closing_units': str(samples[-1][0] - samples[-1][1])}


def gains(source, kind):
    start = source.period.start - timedelta(days=1)
    days = [start + timedelta(days=i) for i in range((source.period.end - start).days + 1)]
    def sample(day):
        owed = D(0)
        if kind == 'morpho':
            gross = sum((source.balance(chain, token, day) for chain, token in MORPHO.items()), D(0))
            coin = 'ethereum:' + MORPHO['ethereum']
        elif kind == 'eth_weth':
            gross = sum((source.native(chain, day) + source.balance(chain, token, day)
                         for chain, token in WETH.items()), D(0))
            coin = 'coingecko:ethereum'
        elif kind == 'avax':
            gross = source.native('avalanche_c', day) + source.balance('avalanche_c', WAVAX, day)
            coin = 'coingecko:avalanche-2'
        else:
            gross = source.balance('ethereum', SPWETH, day)
            raw = source.call('ethereum', SPETH, 'assetsOutstanding()', source.block('ethereum', day))
            if raw == '0x':
                if source.call('ethereum', SPETH, 'totalSupply()', source.block('ethereum', day)) != '0x':
                    raise ValueError('Invalid savings liability state')
            else:
                owed = D(int(raw, 16)) / WAD
            coin = 'coingecko:ethereum'
        return gross, owed, coin
    with ThreadPoolExecutor(max_workers=3) as pool:
        units = list(pool.map(sample, days))
        # No quote is needed to establish zero revaluation on an empty book.
        prices = list(pool.map(lambda day: source.price(units[0][2], day), days)) if any(
            gross != 0 or owed != 0 for gross, owed, _ in units) else [D(0)] * len(days)
    result = revaluation([(gross, owed, price) for (gross, owed, _), price in zip(units, prices, strict=True)])
    # EOD observations cannot prove that intraday exposure was empty or hedged.
    result['quality'] = 'estimate'
    if kind == 'sparklend_nonstable':
        transfers = source.logs('ethereum', [
            {'address': list(OTHER_NONSTABLE_RESERVES), 'topics': [[TRANSFER_TOPIC0], [topic(ETH_ALM)]]},
            {'address': list(OTHER_NONSTABLE_RESERVES), 'topics': [[TRANSFER_TOPIC0], [], [topic(ETH_ALM)]]},
        ])
        def empty(token):
            return source.balance('ethereum', token, days[0]) == 0 and source.balance('ethereum', token, days[-1]) == 0
        with ThreadPoolExecutor(max_workers=3) as pool:
            empty_book = all(pool.map(empty, OTHER_NONSTABLE_RESERVES))
        complete = empty_book and not any(int(row.data, 16) for row in transfers)
        result['scope'] = 'spWETH_less_spETH_assetsOutstanding; other reviewed nonstable reserves checked for empty holdings and transfers'
        if not complete:
            result.update(status='partial', reason='other_nonstable_reserve_positions_need_valuation')
    return result


def native_supply(source, spec):
    venue = next(v for v in source.prime.venues if v.id == spec.venue)
    chain, token = str(venue.chain), venue.token.address.hex
    holder = source.prime.alm[venue.chain].hex
    start = source.period.start - timedelta(days=1)
    days = [start + timedelta(days=i) for i in range((source.period.end - start).days + 1)]
    transfers = source.logs(chain, [
        {'address': [token], 'topics': [[TRANSFER_TOPIC0], [topic(holder)]]},
        {'address': [token], 'topics': [[TRANSFER_TOPIC0], [], [topic(holder)]]},
    ])
    def balance(day):
        return D(balance_of(venue.chain, venue.token.address, source.prime.alm[venue.chain], source.block(chain, day))) / 10**venue.token.decimals
    empty = balance(start) == 0 and balance(source.period.end) == 0 and not any(int(row.data, 16) for row in transfers)
    income = average = D(0)
    if not empty:
        def sample(day):
            block = source.block(chain, day)
            if spec.category == 'C':
                raw = source.call(chain, token, 'scaledBalanceOf(address)', block, int(holder, 16))
                if raw == '0x':
                    return D(0), None
                units = D(int(raw, 16)) / 10**venue.token.decimals
                pool = source.call(chain, token, 'POOL()', block)
                index = source.number(chain, '0x' + pool[-40:], 'getReserveNormalizedIncome(address)', block, int(venue.underlying.address.hex, 16))
                return units, D(index) / 10**27
            units = balance(day)
            raw = source.call(chain, token, 'convertToAssets(uint256)', block, 10**venue.token.decimals)
            price = D(int(raw, 16)) / 10**venue.underlying.decimals if raw != '0x' else None
            if price is not None and venue.underlying.symbol == 'sUSDS':
                underlying = source.number('ethereum', '0xa3931d71877c0e7a3148cb7eb4463524fec27fbd',
                                           'convertToAssets(uint256)', source.block('ethereum', day), WAD)
                price *= D(underlying) / WAD
            return units, price
        with ThreadPoolExecutor(max_workers=8) as pool:
            samples = list(pool.map(sample, days))
        for (q0, p0), (q1, p1) in pairwise(samples):
            if p0 is not None and p1 is not None:
                income += (q0 + q1) / 2 * (p1 - p0)
            average += (q0 * (p0 or D(0)) + q1 * (p1 or D(0))) / 2
        average /= source.period.n_days
    return {'venue': spec.venue, 'family': spec.family, 'label': spec.label, 'status': 'available',
            'chain': chain, 'token_address': token, 'holder': holder,
            'underlying_address': venue.underlying.address.hex,
            'value': str(income), 'average_position_usd': str(average),
            'quality': 'supported' if empty else 'estimate', 'recognition': 'accrual',
            'basis': 'average_daily_scaled_balance_times_pool_index_change' if spec.category == 'C'
                     else 'average_daily_vault_shares_times_net_NAV_change'}


def collect(prime, period, opening_pins, *, source=None, progress=None):
    source = source or Source(prime, period, opening_pins)
    result = {'schema_version': 'spark-onchain.v1', 'period_start': str(period.start), 'cutoff': str(period.end),
              'curator_fees': {}, 'gains': {}, 'native_supplies': {}, 'errors': {}}
    def attempt(key, fn):
        if progress:
            progress(key)
        try:
            with localcontext() as ctx:
                ctx.prec = 60
                return fn()
        except Exception as exc:
            result['errors'][key] = type(exc).__name__
            return {'status': 'unavailable', 'reason': 'reporting_source_unavailable'}
    result['reserve_fees'] = attempt('reserve_fees', lambda: reserve_fees(source))
    for spec in CURATORS:
        result['curator_fees'][spec[0]] = attempt('curator.' + spec[0], lambda spec=spec: curator_fees(source, spec))
    for kind in ('morpho', 'avax', 'eth_weth', 'sparklend_nonstable'):
        result['gains'][kind] = attempt('gains.' + kind, lambda kind=kind: gains(source, kind))
    for spec in (*ALLOCATIONS['spark'], *SPARK_EXTRA):
        result['native_supplies'][spec.venue] = attempt('supply.' + spec.venue, lambda spec=spec: native_supply(source, spec))
    from .spark_completion import collect as complete
    completion = attempt('statement_completion', lambda: complete(source, progress=progress))
    if 'status' not in completion:
        result.update(completion)
    return result
