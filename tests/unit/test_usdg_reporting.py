from datetime import UTC, date, datetime
from decimal import Decimal

from settle.compute import Sources
from settle.domain.config import load_prime_by_id
from settle.domain.period import Period
from settle.domain.primes import Chain
from settle.extract.hypersync import LogRow
from settle.extract.transfer_logs import TRANSFER_TOPIC0, _to_topic_hex
from settle.normalize.incentives import compute_incentive
from settle.normalize.sources.hypersync_balances import HyperSyncBalanceSource
from tests.fixtures.mock_sources import MockBlockResolver


def test_robinhood_alm_and_usdg_reporting_are_configured_with_launch_dates():
    prime = load_prime_by_id('spark')
    venue = next(v for v in prime.venues if v.id == 'S69')
    assert prime.alm[Chain.ROBINHOOD].hex == '0xfd2fd4b046136b540a56c11c75ac679ae7d1db24'
    assert prime.alm_start_dates[Chain.ROBINHOOD] == date(2026, 6, 19)
    assert not venue.skip and not venue.settles
    assert venue.holder_override == prime.alm[Chain.ROBINHOOD]
    source, = venue.incentive['receipt_sources']
    assert source['recipient'] == prime.alm[Chain.ROBINHOOD].hex
    assert source['payer'] == '0x5b01773138f17b38d1987558e576fa193db71a00'
    assert Chain.ROBINHOOD not in prime.chains_at(date(2026, 1, 31))


def test_usdg_income_only_counts_verified_payer_to_alm_receipts():
    prime = load_prime_by_id('spark')
    venue = next(v for v in prime.venues if v.id == 'S69')
    source, = venue.incentive['receipt_sources']
    vault = next(v for v in prime.venues if v.id == 'S63').token.address.hex
    day = date(2026, 9, 1)
    timestamp = int(datetime(2026, 9, 1, 14, tzinfo=UTC).timestamp())
    def transfer(index, sender, recipient, amount):
        return LogRow(100, index, timestamp, venue.token.address.hex, TRANSFER_TOPIC0,
                      _to_topic_hex(bytes.fromhex(sender[2:])),
                      _to_topic_hex(bytes.fromhex(recipient[2:])), None, hex(amount))
    outsider = '0x' + '11'*20
    rows = [
        transfer(0, source['payer'], source['recipient'], 1591972312),
        transfer(1, outsider, source['recipient'], 50_000_000*10**6),  # capital to ALM
        transfer(2, outsider, vault, 100_000_000*10**6),  # retail vault deposit
        transfer(3, source['payer'], vault, 123*10**6),  # a different recipient
    ]
    balances = HyperSyncBalanceSource(fetch_logs=lambda *a, **kw: rows,
                                      resolve_start_block=lambda *a: 0,
                                      decimals_of=lambda *a: 6)
    period = Period(day, day, pin_blocks={Chain.ROBINHOOD: 100})
    result = compute_incentive(prime, venue, period,
                               Sources(balance=balances, block_resolver=MockBlockResolver()))
    assert result.revenue == Decimal('1591.972312')
    assert result.value_som == result.value_eom == 0
    assert result.incentive_accrual['period_receipts'] == '1591.972312'
    assert result.period_inflow == -result.revenue
    assert not result.settlement_enabled
