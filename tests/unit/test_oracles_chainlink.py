from decimal import Decimal

import pytest

from settle.domain.primes import Address, Chain
from settle.extract.oracles import chainlink
from settle.normalize.registry import get_nav_oracle_source

FEED = Address(bytes.fromhex('11'*20))


def setup_feed(monkeypatch, *, decimals=8, answer=234512345678, updated=9990, round_id=1):
    def call(chain, feed, selector, block):
        if selector == chainlink.SEL_DECIMALS:
            return '0x' + format(decimals, '064x')
        words = (round_id, answer % 2**256, updated, updated, round_id)
        return '0x' + ''.join(format(w, '064x') for w in words)
    monkeypatch.setattr(chainlink.rpc, 'eth_call', call)
    monkeypatch.setattr(chainlink.rpc, 'block_timestamp', lambda *a: 10000)


def test_chainlink_price_reads_decimal_precision_and_uses_pinned_block(monkeypatch, tmp_cache_dir):
    setup_feed(monkeypatch)
    source = get_nav_oracle_source('chainlink')
    assert source.nav_at('ethereum', FEED.value, 100) == Decimal('2345.12345678')
    setup_feed(monkeypatch, decimals=18, answer=3000*10**18)
    assert chainlink.read(Chain.ETHEREUM, FEED, 200) == 3000


@pytest.mark.parametrize('values', [
    {'answer': 0}, {'answer': -1}, {'updated': 0}, {'updated': 10001},
    {'round_id': 0}, {'decimals': 37},
])
def test_invalid_feed_values_are_rejected(monkeypatch, tmp_cache_dir, values):
    setup_feed(monkeypatch, **values)
    with pytest.raises(chainlink.ChainlinkReadError):
        chainlink.read(Chain.ETHEREUM, FEED, 100)


def test_stale_or_truncated_feed_is_rejected(monkeypatch, tmp_cache_dir):
    setup_feed(monkeypatch, updated=1)
    monkeypatch.setattr(chainlink.rpc, 'block_timestamp', lambda *a: 100000)
    with pytest.raises(chainlink.ChainlinkReadError, match='stale'):
        chainlink.read(Chain.ETHEREUM, FEED, 100)
    monkeypatch.setattr(chainlink.rpc, 'eth_call', lambda *a: '0x' + '00'*32)
    with pytest.raises(chainlink.ChainlinkReadError, match='Malformed'):
        chainlink.read(Chain.ETHEREUM, FEED, 200)
