"""Official SOFR snapshots for daily publication; never infer a holiday from a missing row."""
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yaml
from psycopg.types.json import Jsonb

from settle.domain.subsidy import (
    ReferenceRateHistory,
    ScheduledReferenceRateHistory,
    load_reference_rates_for,
)

from .verification import digest

SOURCE = 'https://markets.newyorkfed.org/api/rates/secured/sofr/search.json'
CALENDAR = Path(__file__).resolve().parents[3] / 'config' / 'sofr_calendar.yaml'


class ReferenceRatesUnavailable(RuntimeError):
    """A required official observation is unavailable; retain the prior publication."""


def effective_day(day, calendar):
    lo, hi = date.fromisoformat(calendar['valid_from']), date.fromisoformat(calendar['valid_through'])
    closed = {date.fromisoformat(d) for d in calendar['full_closures']}
    while lo <= day <= hi:
        if day.weekday() < 5 and day not in closed:
            return day
        day -= timedelta(days=1)
    raise ReferenceRatesUnavailable('Reference-rate calendar does not cover required date')


def publication_time(day, calendar):
    """SOFR for a trading date is published at 08:00 New York next publication day."""
    day += timedelta(days=1)
    extra = set(calendar.get('extra_publication_days', []))
    while True:
        if str(day) in extra or effective_day(day, calendar) == day:
            return datetime.combine(day, time(8), ZoneInfo('America/New_York'))
        day += timedelta(days=1)


def available_cutoff(prime, cutoff, now):
    """Latest complete cutoff whose required rate is scheduled to be available.

    This is calendar-based, never inferred from missing API observations. After
    publication is due, a missing rate must still fail closed in fetch_sofr.
    """
    if now.tzinfo is None:
        raise ValueError('publication time must be timezone-aware')
    if not prime.subsidy.enabled:
        return cutoff
    configured = load_reference_rates_for(prime.subsidy)
    calendar = yaml.safe_load(CALENDAR.read_text())
    while True:
        kind = (configured.kind_at(cutoff) if isinstance(configured, ScheduledReferenceRateHistory)
                else configured.kind)
        if kind != 'sofr' or publication_time(effective_day(cutoff, calendar), calendar) <= now:
            return cutoff
        cutoff -= timedelta(days=1)


def fetch_sofr(required, *, end=None):
    """Refresh the small MTD window on each attempt, including possible corrections."""
    end = end or max(required)
    response = requests.get(SOURCE, params={'startDate': str(min(required)),
                                           'endDate': str(end)}, timeout=(10, 30))
    response.raise_for_status()
    payload = response.json(parse_float=Decimal)
    if not isinstance(payload, dict) or not isinstance(payload.get('refRates'), list):
        raise ReferenceRatesUnavailable('Invalid SOFR response')
    observations = {}
    try:
        for row in payload['refRates']:
            day = date.fromisoformat(row['effectiveDate'])
            if row['type'] != 'SOFR' or day in observations or not min(required) <= day <= end:
                raise ValueError('invalid series, duplicate or out-of-window observation')
            rate = Decimal(str(row['percentRate'])) / Decimal(100)
            if not rate.is_finite() or not Decimal('-1') < rate < Decimal('1'):
                raise ValueError('invalid rate')
            observations[day] = {'effective_date': str(day), 'apr': str(rate),
                                 'revision_indicator': str(row.get('revisionIndicator', ''))}
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise ReferenceRatesUnavailable('Invalid SOFR observation') from exc
    if observations.keys() - required:
        raise ReferenceRatesUnavailable('Official SOFR observations conflict with the configured calendar')
    missing = required - observations.keys()
    if missing:
        raise ReferenceRatesUnavailable(f'Missing official SOFR effective dates: {sorted(missing)}')
    return [observations[d] for d in sorted(required)]


@dataclass
class PreparedRates:
    history: ReferenceRateHistory | ScheduledReferenceRateHistory
    provenance: dict
    snapshot_id: str


def prepare(conn, prime, cutoff):
    if not prime.subsidy.enabled:
        return None
    calendar = yaml.safe_load(CALENDAR.read_text())
    configured = load_reference_rates_for(prime.subsidy)
    required = {}
    carry = {}
    day = cutoff.replace(day=1)
    while day <= cutoff:
        kind = configured.kind_at(day) if isinstance(configured, ScheduledReferenceRateHistory) else configured.kind
        effective = effective_day(day, calendar)
        required.setdefault(kind, set()).add(effective)
        if effective != day:
            carry[str(day)] = str(effective)
        day += timedelta(days=1)
    series = {}
    histories = {}
    for kind, dates in required.items():
        if kind == 'sofr':
            rows = fetch_sofr(dates, end=cutoff)
            source = SOURCE
        else:
            # Pre-SOFR historical months remain reproducible from the existing
            # configuration, but cannot silently carry past a missing business day.
            history = configured.histories[kind] if isinstance(configured, ScheduledReferenceRateHistory) else configured
            known = {r.effective_date: r.ref_rate_apr for r in history.rates.itertuples()}
            if dates - known.keys():
                raise ReferenceRatesUnavailable('Missing configured historical reference-rate observations')
            rows = [{'effective_date': str(d), 'apr': str(known[d])} for d in sorted(dates)]
            source = 'config/subsidy_reference_rates.yaml'
        series[kind] = {'source': source, 'observations': rows}
        histories[kind] = ReferenceRateHistory(pd.DataFrame([
            {'effective_date': date.fromisoformat(r['effective_date']), 'ref_rate_apr': Decimal(r['apr'])}
            for r in rows]), kind)
    content = {'calendar_version': digest(calendar), 'series': series, 'carry_forward_dates': carry}
    snapshot_id = digest(content)
    conn.execute('''INSERT INTO revenue_reference_snapshots(snapshot_id, content)
        VALUES (%s,%s) ON CONFLICT DO NOTHING''', (snapshot_id, Jsonb(content)))
    fetched_at = conn.execute('SELECT first_observed_at FROM revenue_reference_snapshots WHERE snapshot_id=%s',
                              (snapshot_id,)).fetchone()[0]
    history = (replace(configured, histories=histories)
               if isinstance(configured, ScheduledReferenceRateHistory) else histories[configured.kind])
    return PreparedRates(history, {'reference_rates': {**content, 'snapshot_id': snapshot_id,
                        'first_observed_at': fetched_at.isoformat(), 'coverage_complete': True}}, snapshot_id)
