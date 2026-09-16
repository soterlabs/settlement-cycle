from datetime import date
from decimal import Decimal

import pytest
import yaml

from settle.domain.subsidy import subsidised_apr
from settle.revenue import reference_rates as rates


def test_holiday_calendar_distinguishes_full_from_early_closes():
    calendar = yaml.safe_load(rates.CALENDAR.read_text())
    assert rates.effective_day(date(2026, 9, 7), calendar) == date(2026, 9, 4)
    assert rates.effective_day(date(2026, 9, 6), calendar) == date(2026, 9, 4)
    assert rates.effective_day(date(2026, 4, 3), calendar) == date(2026, 4, 3)
    assert rates.effective_day(date(2026, 11, 27), calendar) == date(2026, 11, 27)
    assert rates.effective_day(date(2027, 3, 26), calendar) == date(2027, 3, 25)
    assert rates.effective_day(date(2027, 12, 31), calendar) == date(2027, 12, 31)
    with pytest.raises(rates.ReferenceRatesUnavailable, match='calendar'):
        rates.effective_day(date(2028, 1, 3), calendar)


def response(monkeypatch, rows):
    class Response:
        def raise_for_status(self): pass
        def json(self, **kwargs): return {'refRates': rows}
    monkeypatch.setattr(rates.requests, 'get', lambda *a, **kw: Response())


def row(day='2026-09-14', rate='3.62', **kwargs):
    return {'effectiveDate': day, 'type': 'SOFR', 'percentRate': rate, **kwargs}


def test_sofr_uses_effective_date_and_converts_percent_to_apr_exactly(monkeypatch):
    response(monkeypatch, [row(revisionIndicator='*')])
    result = rates.fetch_sofr({date(2026, 9, 14)})
    assert result == [{'effective_date': '2026-09-14', 'apr': '0.0362', 'revision_indicator': '*'}]


@pytest.mark.parametrize('rows', [[], [row(), row()], [row(rate='NaN')],
                                 [row(rate='Infinity')], [row(rate=None)],
                                 [row('2026-09-15')], [{'effectiveDate': 'bad'}]])
def test_missing_and_invalid_observations_never_become_carry_forward(monkeypatch, rows):
    response(monkeypatch, rows)
    with pytest.raises(rates.ReferenceRatesUnavailable):
        rates.fetch_sofr({date(2026, 9, 14)})


@pytest.mark.parametrize('reference', ['0.035', '0.04', '0.05'])
def test_reference_at_or_above_base_cannot_increase_borrowing_rate(reference):
    base, ref = Decimal('0.04'), Decimal(reference)
    actual = subsidised_apr(base, ref, 8, 24)
    assert actual == min(base, ref + (base-ref)*Decimal(8)/Decimal(24))


def test_an_official_print_on_a_configured_closure_blocks_instead_of_being_ignored(monkeypatch):
    response(monkeypatch, [row('2026-09-04'), row('2026-09-07')])
    with pytest.raises(rates.ReferenceRatesUnavailable, match='calendar'):
        rates.fetch_sofr({date(2026, 9, 4)}, end=date(2026, 9, 7))
