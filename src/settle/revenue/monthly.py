"""Explicit monthly finalization of one immutable, complete daily MTD result.

No provider reads or API writes. Interest is independently recalculated using
saved daily debt/deductions and the pinned reference-rate observations. The
ordinary monthly writer still adds distribution rewards; GAR is refreshed here.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from types import UnionType
from typing import get_args, get_origin, get_type_hints

import pandas as pd
import yaml

from settle.compute.gar import compute_gar
from settle.compute.sky_revenue import compute_sky_revenue_daily
from settle.domain.monthly_pnl import MonthlyPnL, SDEDailyBreakdown
from settle.domain.subsidy import (
    ReferenceRateHistory,
    ScheduledReferenceRateHistory,
    load_reference_rates_for,
)

from . import store
from .reference_rates import CALENDAR, effective_day
from .verification import canonical, digest

# Daily result rates are serialized as floats by the existing calculator.
# Allow sub-microdollar round-trip noise, never round settlement dollars.
_TOLERANCE = Decimal('0.000001')


def _decode(kind, value):
    """Strict inverse of canonical for result dataclasses (no missing defaults)."""
    origin, args = get_origin(kind), get_args(kind)
    if origin is UnionType:
        if value is None and type(None) in args:
            return None
        return _decode(next(t for t in args if t is not type(None)), value)
    if is_dataclass(kind):
        if not isinstance(value, dict) or set(value) != {f.name for f in fields(kind)}:
            raise ValueError(f'incompatible snapshot schema: {kind.__name__}')
        hints = get_type_hints(kind)
        decoded = {k: _decode(hints[k], v) for k, v in value.items()}
        if kind is SDEDailyBreakdown:
            decoded['daily'] = [
                {'block_date': date.fromisoformat(r['block_date']),
                 'cum_value': _decode(Decimal, r['cum_value']),
                 'uncapped_value': _decode(Decimal, r['uncapped_value'])}
                for r in value['daily']
            ]
        return kind(**decoded)
    if origin is list:
        if not isinstance(value, list):
            raise ValueError('expected list')
        return [_decode(args[0], v) for v in value]
    if origin is dict:
        if not isinstance(value, dict):
            raise ValueError('expected dict')
        return {_decode(args[0], k): _decode(args[1], v) for k, v in value.items()}
    if kind is Decimal:
        if not isinstance(value, str):
            raise ValueError('expected lossless Decimal string')
        result = Decimal(value)
        if not result.is_finite():
            raise ValueError('non-finite amount')
        return result
    if kind is date:
        return date.fromisoformat(value)
    if isinstance(kind, type) and issubclass(kind, Enum):
        return kind(value)
    if type(value) is not kind:
        raise ValueError(f'invalid {kind} value')
    return value


def _close(actual, expected, label):
    actual, expected = Decimal(str(actual)), Decimal(str(expected))
    if not actual.is_finite() or not expected.is_finite() or abs(actual - expected) > _TOLERANCE:
        raise ValueError(f'{label} does not reconcile: {actual} != {expected}')


def _reference_history(prime, period, provenance):
    if not prime.subsidy.enabled:
        return None
    saved = provenance.get('reference_rates', {})
    if not {'calendar_version', 'series', 'carry_forward_dates'} <= saved.keys():
        raise ValueError('missing reference-rate snapshot')
    content = {k: saved[k] for k in ('calendar_version', 'series', 'carry_forward_dates')}
    calendar = yaml.safe_load(CALENDAR.read_text())
    if (saved.get('coverage_complete') is not True or digest(content) != saved.get('snapshot_id')
            or content['calendar_version'] != digest(calendar)):
        raise ValueError('invalid reference-rate snapshot')
    configured = load_reference_rates_for(prime.subsidy)
    histories = {}
    for kind, series in content['series'].items():
        observations = series['observations']
        dates = [r['effective_date'] for r in observations]
        if len(dates) != len(set(dates)):
            raise ValueError('duplicate reference observations')
        histories[kind] = ReferenceRateHistory(pd.DataFrame([
            {'effective_date': date.fromisoformat(r['effective_date']),
             'ref_rate_apr': _decode(Decimal, r['apr'])} for r in observations]), kind)
    day = period.start
    while day <= period.end:
        kind = (configured.kind_at(day) if isinstance(configured, ScheduledReferenceRateHistory)
                else configured.kind)
        required = effective_day(day, calendar)
        if kind not in histories or required not in set(histories[kind].rates['effective_date']):
            raise ValueError(f'missing reference observation for {day}')
        if required != day and content['carry_forward_dates'].get(str(day)) != str(required):
            raise ValueError(f'invalid reference carry for {day}')
        day += timedelta(days=1)
    return (replace(configured, histories=histories)
            if isinstance(configured, ScheduledReferenceRateHistory) else histories[configured.kind])


def validate_interest(pnl, prime, provenance):
    """Re-run the canonical interest formula, including subsidy and deductions.

    sde_av already includes the PSM USDC slice; do not count it a second time.
    Sky's final claim also includes SDE income and subtracts spread reimbursements.
    """
    rows = pnl.sky_revenue_daily
    expected_days = [str(pnl.period.start + timedelta(days=i)) for i in range(pnl.period.n_days)]
    if [r['date'] for r in rows] != expected_days:
        raise ValueError('incomplete or duplicated daily borrowing-cost coverage')

    def series(source, target):
        return pd.DataFrame([{'block_date': date.fromisoformat(r['date']),
                              target: _decode(Decimal, r[source])} for r in rows])

    basin_rows = []
    for row in rows:
        day = date.fromisoformat(row['date'])
        active = prime.basin_idle_usds is not None and day >= prime.basin_idle_usds.effective_from
        # Old snapshots are compatible only before activation / for other primes.
        if active and not {'basin_idle', 'basin_ilk_debt'} <= row.keys():
            raise ValueError('missing Basin idle inputs; recompute daily revenue')
        idle = _decode(Decimal, row.get('basin_idle', '0'))
        debt = _decode(Decimal, row.get('basin_ilk_debt', '0'))
        if not active and (idle != 0 or debt != 0):
            raise ValueError('Basin idle inputs outside configured effective scope')
        basin_rows.append({'block_date': day, 'cum_balance': idle, 'ilk_debt': debt})

    ssr = pd.DataFrame([{'effective_date': date.fromisoformat(r['date']),
                         'ssr_apy': Decimal(str(r['ssr_apy']))} for r in rows])
    total, daily, _ = compute_sky_revenue_daily(
        pnl.period, series('cum_debt', 'cum_debt'), series('alm_usds', 'cum_balance'), ssr,
        psm_usds=series('psm_usds', 'cum_usds_leg'),
        sde_asset_value=series('sde_av', 'cum_value'),
        curve_idle_usds=series('curve_idle', 'cum_balance'),
        lending_idle_usds=series('lending_idle', 'cum_balance'),
        basin_idle_usds=pd.DataFrame(basin_rows),
        subsidy_config=prime.subsidy,
        ref_rate_history=_reference_history(prime, pnl.period, provenance),
    )
    for saved, computed in zip(rows, daily.to_dict('records'), strict=True):
        for key in ('utilized', 'daily_sky_rev', 'daily_sky_rev_gross'):
            _close(saved[key], computed[key], f"{saved['date']} {key}")
    _close(pnl.sky_revenue_gross, daily['daily_sky_rev_gross'].sum(), 'gross interest')
    _close(pnl.sde_revenue, sum(v.sd_revenue for v in pnl.venue_breakdown), 'SDE revenue')
    _close(pnl.susds_spread_reimbursement,
           sum(v.susds_spread_reimbursement for v in pnl.venue_breakdown)
           + pnl.curve_susds_spread + pnl.psm3_susds_spread, 'spread reimbursement')
    _close(pnl.sky_revenue, total + pnl.sde_revenue - pnl.susds_spread_reimbursement,
           'Sky claim')
    return total


def finalize(record, prime, month, versions, *, today=None):
    """Validate an explicitly selected revision; return result + audit sources.

    Code/config/manual-input versions must match exactly. This intentionally
    requires a fresh daily calculation after methodology changes. The rate
    snapshot remains frozen, not silently replaced by today's observations.
    """
    today = today or datetime.now(UTC).date()
    if month.last_day >= today:
        raise ValueError('month must be complete in UTC')
    if not record or record['prime'] != prime.id or record['cutoff'] != str(month.last_day):
        raise ValueError('missing complete month-end revision for this prime')
    provenance = record['input_provenance']
    if (record['code_version'] != versions.code
            or record['configuration_version'] != versions.configuration
            or provenance.get('manual_input_revision') != versions.inputs):
        raise ValueError('snapshot code/config/manual-input version differs; refresh daily result')
    identity = {k: record[k] for k in ('prime', 'cutoff', 'opening_pins', 'closing_pins',
                                      'code_version', 'configuration_version', 'input_revision')}
    if digest(identity) != record['revision_id'] or digest(record['result']) != record['result_hash']:
        raise ValueError('snapshot identity/result hash mismatch')
    pnl = _decode(MonthlyPnL, record['result'])
    if (pnl.prime_id != prime.id or pnl.month != month or pnl.period.start != month.first_day
            or pnl.period.end != month.last_day):
        raise ValueError('snapshot period/prime mismatch')
    if (canonical(pnl.pin_blocks_som) != record['opening_pins']
            or canonical(pnl.period.pin_blocks) != record['closing_pins']
            or set(pnl.pin_blocks_som) != prime.chains or set(pnl.period.pin_blocks) != prime.chains
            or any(not 0 < b < pnl.period.pin_blocks[c] for c, b in pnl.pin_blocks_som.items())):
        raise ValueError('snapshot block pins mismatch')
    ids = [v.venue_id for v in pnl.venue_breakdown + pnl.display_only_breakdown]
    if len(ids) != len(set(ids)) or set(ids) - {v.id for v in prime.venues}:
        raise ValueError('invalid venue inventory')
    _close(pnl.prime_agent_revenue,
           sum(v.revenue for v in pnl.venue_breakdown) + pnl.psm3_susds_appreciation,
           'supply revenue')
    if pnl.distribution_rewards != 0 or pnl.dr_breakdown:
        raise ValueError('daily snapshot already contains monthly distribution rewards')
    interest = validate_interest(pnl, prime, provenance)
    # GAR depends on the monthly consolidated report, so refresh it at settlement.
    gar, basis = compute_gar(prime, month)
    pnl = replace(pnl, gar=gar, gar_basis=basis,
                  monthly_pnl=pnl.prime_agent_revenue + pnl.agent_rate
                  + pnl.chronicle_points + gar - pnl.sky_revenue)
    return pnl, {
        'daily_revenue_revision': record['revision_id'],
        'daily_revenue_result_hash': record['result_hash'],
        'daily_revenue_code_version': record['code_version'],
        'daily_revenue_configuration_version': record['configuration_version'],
        'daily_revenue_input_revision': record['input_revision'],
        'reference_rate_snapshot': provenance.get('reference_rates', {}).get('snapshot_id', ''),
        'borrowing_costs_recalculated_usd': str(interest),
        'borrowing_costs_validation': 'canonical formula, saved inputs; tolerance USD 0.000001',
    }


def from_database(conn, prime, month, revision):
    record = store.read(conn, prime.id, cutoff=month.last_day, revision=revision)
    return finalize(record, prime, month, store.capture_versions())
