"""Official daily DFEDTARL observations in fractional units; never forward-fill."""
import csv
from datetime import date
from decimal import Decimal
from io import StringIO

import requests

from .cache import cached
from .publication import source_operation

SOURCE = 'https://fred.stlouisfed.org/graph/' + 'fredgraph.csv'


@cached(source_id='fred.dfedtar_lower.v2')
@source_operation
def _observations(start: date, end: date) -> list[tuple[date, Decimal]]:
    response = requests.get(SOURCE, params={'id': 'DFEDTARL', 'cosd': str(start),
                                           'coed': str(end)}, timeout=(10, 30))
    response.raise_for_status()
    rows = csv.DictReader(StringIO(response.text))
    if rows.fieldnames != ['observation_date', 'DFEDTARL']:
        raise ValueError('Invalid DFEDTARL response')
    values = {}
    for row in rows:
        day = date.fromisoformat(row['observation_date'])
        if not start <= day <= end:
            continue
        value = Decimal(row['DFEDTARL']) / 100
        if not value.is_finite():
            raise ValueError(f'Invalid DFEDTARL observation for {day}')
        values[day] = value
    if len(values) != (end - start).days + 1:
        raise ValueError('Incomplete DFEDTARL daily coverage')
    # Date values survive the JSONB codec; dictionary keys become strings.
    return sorted(values.items())


def fed_lower(start: date, end: date) -> dict[date, Decimal]:
    """Reconstruct date keys after either a fresh read or a persisted-cache hit."""
    return dict(_observations(start, end))
