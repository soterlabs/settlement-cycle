"""Pinned Chainlink AggregatorV3 USD prices for reporting-only positions.

Read decimals dynamically and validate the signed answer and update timestamp.
https://docs.chain.link/data-feeds/api-reference
"""
from decimal import Decimal

from ...domain.primes import Address, Chain
from .. import rpc
from ..cache import cached

SEL_DECIMALS = '0x313ce567'
SEL_LATEST_ROUND_DATA = '0xfeaf968c'
MAX_AGE_SECONDS = 24 * 60 * 60


class ChainlinkReadError(ValueError):
    """The pinned feed has no valid, sufficiently recent price."""


@cached(source_id='chainlink.usd_price.v1')
def read(chain: Chain, oracle: Address, block: int) -> Decimal:
    decimals_raw = rpc.eth_call(chain, oracle, SEL_DECIMALS, block).removeprefix('0x')
    round_raw = rpc.eth_call(chain, oracle, SEL_LATEST_ROUND_DATA, block).removeprefix('0x')
    if len(decimals_raw) != 64 or len(round_raw) != 5 * 64:
        raise ChainlinkReadError('Malformed Chainlink decimals or round data')
    decimals = int(decimals_raw, 16)
    if not 0 <= decimals <= 36:
        raise ChainlinkReadError('Invalid Chainlink decimals')
    words = [int(round_raw[i:i+64], 16) for i in range(0, len(round_raw), 64)]
    round_id, answer, _, updated_at, _ = words
    if round_id == 0 or answer == 0 or answer >= 2**255:
        raise ChainlinkReadError('Chainlink answer must be positive')
    timestamp = rpc.block_timestamp(chain, block)
    if updated_at == 0 or not 0 <= timestamp - updated_at <= MAX_AGE_SECONDS:
        raise ChainlinkReadError('Chainlink price is stale or has an invalid update timestamp')
    return Decimal(answer) / Decimal(10 ** decimals)
