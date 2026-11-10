"""Offline component replay for the venues omitted from historical MSC.

Uses production pricing/capital/revenue functions against retained RPC/log
witnesses. This is not a fresh all-token monthly settlement calculation.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal as D
from unittest.mock import patch

from settle.compute.prime_agent_revenue import VenueRevenueInputs, compute_venue_revenue
from settle.domain import Address, Chain, Month, Period
from settle.domain.config import load_prime_by_id
from settle.extract import rpc
from settle.extract.hypersync import LogRow
from settle.normalize.positions import (
    _atoken_external_revenue_usd,
    _atoken_index_weighted_inflow,
    _shares_to_usd_inflow_timeseries,
    _uniswap_v3_inflow_timeseries,
    _uniswap_v3_value,
)
from settle.normalize.sources.hypersync_balances import HyperSyncBalanceSource
from settle.normalize.sources.hypersync_lp import HyperSyncV3PositionSource
from settle.revenue.verification import canonical


def query_key(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


class Witnesses:
    def __init__(self, document):
        self.document = document

    def post(self, url, method, params):
        chain = {'https://ethereum.offline.invalid': 'ethereum',
                 'https://base.offline.invalid': 'base'}.get(url)
        if chain is None:
            raise ValueError('Unrecorded reconciliation RPC chain')
        key = query_key([chain, method, params])
        if key not in self.document['rpc']:
            raise ValueError(f'Missing pinned reconciliation RPC witness: {chain} {method}')
        return self.document['rpc'][key]

    def logs(self, chain, selections, start, end, **kwargs):
        key = query_key([chain, selections, start, end, kwargs])
        if key not in self.document['log_queries']:
            raise ValueError('Missing complete reconciliation log query')
        return [LogRow(**r) for r in self.document['log_queries'][key]]

    def block_at_or_before(self, chain, anchor):
        key = query_key([chain, int(anchor.timestamp())])
        if key not in self.document['boundaries']:
            raise ValueError('Missing pinned reconciliation daily boundary')
        block = self.document['boundaries'][key]
        if not (rpc.block_timestamp(Chain(chain), block) <= int(anchor.timestamp())
                < rpc.block_timestamp(Chain(chain), block + 1)):
            raise ValueError('Reconciliation daily boundary is not the final block')
        return block

    def block_to_date(self, chain, block):
        return datetime.fromtimestamp(rpc.block_timestamp(Chain(chain), block), UTC).date()


def replay(document):
    evidence = Witnesses(document)
    balance_source = HyperSyncBalanceSource(fetch_logs=evidence.logs,
                                           resolve_start_block=lambda *_: 0)
    def balance(chain, token, holder, block):
        return rpc.balance_of(Chain(chain), Address(token), Address(holder), block)
    def scaled(chain, token, holder, block):
        return rpc.scaled_balance_of(Chain(chain), Address(token), Address(holder), block)
    output = {}
    with patch.dict(os.environ, {
        'SETTLE_NO_CACHE': '1', 'SETTLE_REQUIRE_POSTGRES': '0',
        'ETH_RPC': 'https://ethereum.offline.invalid', 'BASE_RPC': 'https://base.offline.invalid',
    }), patch('settle.extract.rpc._post', evidence.post), \
            patch('settle.extract.hypersync_store.fetch_logs', evidence.logs), \
            patch('settle.extract.hypersync.find_block_at_or_before',
                  lambda chain, stamp: evidence.block_at_or_before(chain, datetime.fromtimestamp(stamp, UTC))):
        prime = load_prime_by_id('spark')
        for vid in ['S72', 'S70']:
            key = f'spark/2026-09/{vid}'
            som, eom = document['results'][key]['pins']
            period = Period.from_month(Month(2026, 9), {Chain.ETHEREUM: eom})
            venue = next(v for v in prime.venues if v.id == vid)
            holder = prime.alm[venue.chain].value
            scale = D(10**venue.token.decimals)
            if vid == 'S72':
                def price(block, v=venue):
                    raw = rpc.eth_call(v.chain, v.token.address,
                                       '0x07a2d13a' + format(10**v.token.decimals, '064x'), block)
                    return D(int(raw, 16)) / 10**v.underlying.decimals
                values = []
                for block in [som, eom]:
                    amount = balance(venue.chain.value, venue.token.address.value, holder, block)
                    values.append(D(amount) / scale * price(block) if amount else D(0))
                flows = _shares_to_usd_inflow_timeseries(
                    prime, venue, period, balance_source=balance_source,
                    block_resolver=evidence, price_at_block=price, som_block=som,
                    balance_at=balance, period_only=True)
                external = D(0)
            else:
                values = [D(balance(venue.chain.value, venue.token.address.value, holder, b)) / scale
                          for b in [som, eom]]
                def events(chain, token, owner, start, end):
                    return [(b - 1, b, evidence.block_to_date(chain, b))
                            for b in balance_source.atoken_event_blocks(chain, token, owner, start, end)]
                flows = _atoken_index_weighted_inflow(
                    prime, venue, som, eom, period_end_date=period.end,
                    balance_at=balance, scaled_balance_at=scaled, transfer_event_blocks=events)
                external = _atoken_external_revenue_usd(prime, venue, period)
            result = compute_venue_revenue(period, VenueRevenueInputs(
                venue=venue, value_som=values[0], value_eom=values[1],
                inflow_timeseries=flows, external_revenue=external))
            output[key] = {'venue': canonical(result), 'flows': canonical(flows.to_dict('records')),
                           'pins': [som, eom]}
        prime = load_prime_by_id('grove')
        venue = next(v for v in prime.venues if v.id == 'E44')
        source = HyperSyncV3PositionSource()
        for month in [7, 8, 9]:
            key = f'grove/2026-{month:02d}/E44'
            som, eom = document['results'][key]['pins']
            period = Period.from_month(Month(2026, month), {Chain.ETHEREUM: eom})
            values = [_uniswap_v3_value(prime, venue, b, source=source) for b in [som, eom]]
            flows = _uniswap_v3_inflow_timeseries(
                prime, venue, som, eom, source=source,
                block_to_date=lambda b: evidence.block_to_date('ethereum', b))
            result = compute_venue_revenue(period, VenueRevenueInputs(
                venue=venue, value_som=values[0], value_eom=values[1], inflow_timeseries=flows))
            output[key] = {'venue': canonical(result), 'flows': canonical(flows.to_dict('records')),
                           'pins': [som, eom]}
    if output != document['results']:
        raise ValueError('Production venue replay differs from captured component results')
    return output
