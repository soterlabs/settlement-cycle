"""The September omissions must route income separately from borrowed capital."""
import json
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pandas as pd

from settle.domain.config import load_prime_by_id
from settle.domain.period import Month, Period
from settle.domain.primes import Chain
from settle.normalize.positions import _cat_a_capital_inflow_timeseries
from tests.fixtures.mock_sources import MockBalanceSource


def test_verified_issuer_receipts_are_income_not_capital():
    prime = load_prime_by_id('spark')
    raw = next(v for v in prime.venues if v.id == 'S64')
    assert raw.settles and raw.external_yield_source and not raw.cof_excluded
    logs = json.loads((Path(__file__).parents[1] / 'fixtures/spark_rlusd_issuer_september_2026.json').read_text())
    assert len(logs) == 6
    assert {r['address'] for r in logs} == {raw.token.address.hex}
    issuer = bytes.fromhex(logs[0]['topic1'][-40:])
    assert issuer not in {a.value for a in prime.external_sources_for_period(Chain.ETHEREUM, date(2026, 8, 1))}
    allowed = {a.value for a in prime.external_sources_for_period(Chain.ETHEREUM, date(2026, 9, 1))}
    assert issuer in allowed
    amounts = [D(int(r['data'], 16)) / D(10**18) for r in logs]
    assert sum(amounts) == D('1373208')
    source = MockBalanceSource()
    source.inflow_by_counterparty = lambda **kw: pd.DataFrame({
        'block_date': [date(2026, 9, 15)] * 8,
        'counterparty': [issuer] * 6 + [bytes.fromhex('11' * 20)] * 2,
        'signed_amount': [*amounts, D(500), D(-100)],
    })
    result = _cat_a_capital_inflow_timeseries(prime, raw,
        Period.from_month(Month(2026, 9), {Chain.ETHEREUM: 26093737}),
        balance_source=source, external_sources=allowed)
    assert result['daily_inflow'].sum() == 400  # Internal cash still capital.
    assert sum(amounts) + 400 - result['daily_inflow'].sum() == D('1373208')
    assert not next(v for v in prime.venues if v.id == 'S67').settles  # No accrual/cash double count.


def test_both_receipt_positions_have_unique_chargeable_msc_coverage():
    prime = load_prime_by_id('spark')
    expected = {'S70': '0x59275fb72c8004f44ba44432e25082932fd677f1',
                'S72': '0xfc8c624b6080a0a780583799f2a862de936f6e22'}
    for key, address in expected.items():
        positions = [v for v in prime.venues if v.token.address.hex == address]
        assert len(positions) == 1
        venue = positions[0]
        assert venue.id == key and venue.settles and venue.reporting_view.enabled
        assert not venue.cof_excluded and not venue.skip
        assert venue.underlying.symbol == 'RLUSD' and venue.token.decimals == 18
    assert next(v for v in prime.venues if v.id == 'S72').start_date == date(2026, 9, 14)
