"""Offline, fixed September S1/E10 settlement refresh; no API publication.

Uses checked-in immutable baselines and narrowly collected Ethereum inputs.
This is an auditable one-off close operation, not a generic refresh interface.
Run with PYTHONPATH=src python scripts/refresh_september_selected_venues.py
--output DIR [--write-reports]. Other prime/venue results are never replayed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pandas as pd

from settle.compute.prime_agent_revenue import VenueRevenueInputs, compute_venue_revenue
from settle.compute.sky_revenue import compute_sky_revenue_daily
from settle.domain.config import load_prime_by_id
from settle.domain.monthly_pnl import MonthlyPnL
from settle.domain.sde import load_sde_table
from settle.domain.subsidy import ReferenceRateHistory
from settle.normalize.redemption_settlements import (
    match_redemptions,
    policy_for,
    restore_redemption_capital,
)
from settle.revenue.monthly import _decode
from settle.revenue.verification import canonical, digest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'docs/september-2026-close/selective-refresh'
BASELINES = {
    'spark': 'c0afd17ba83c7877953315a1791569052999ac9d8a4106df19a5ef6ebc4205d8',
    'grove': '4636fa7f19683781d1c5c756a0e2bdf3882f2cc19b9704b0102fd3fed94589dd',
}
NEW_DEFAULTS = {
    'redemption_revenue_adjustment': '0', 'redemption_settlements': [],
    'outstanding_redemptions': [], 'unmatched_redemption_cash': [],
    'redemption_capital_outflows': [],
}


def baseline(prime):
    path = DATA / f'{prime}-baseline.json'
    if hashlib.sha256(path.read_bytes()).hexdigest() != BASELINES[prime]:
        raise ValueError('Expected the immutable, pre-PR218 September baseline')
    saved = json.loads(path.read_text())
    result = copy.deepcopy(saved['result'])
    # These pinned pre-analytics snapshots have no allocation tracing payload.
    # Preserve strict decoding elsewhere; do not trigger a capital replay here.
    result['allocation_financing'] = None
    # Explicit migration for these two hash-pinned snapshots only. Do not relax
    # the production strict decoder or accept a previously refreshed result.
    for row in result['venue_breakdown'] + result['display_only_breakdown']:
        if set(NEW_DEFAULTS).intersection(row):
            raise ValueError('Baseline already contains redemption metadata')
        row.update(copy.deepcopy(NEW_DEFAULTS))
    pnl = _decode(MonthlyPnL, result)
    assert str(pnl.month) == '2026-09'
    assert pnl.period.start == date(2026, 9, 1) and pnl.period.end == date(2026, 9, 30)
    assert saved['input_provenance']['reference_rates']['coverage_complete']
    return saved, pnl


def reprice(pnl, prime, sde_delta):
    """Recompute interest from saved debt/rates/deductions, changing only E10 AV."""
    rows = pnl.sky_revenue_daily
    assert len(rows) == 30
    def series(source, target, changed=False):
        return pd.DataFrame([
            {'block_date': date.fromisoformat(r['date']),
             target: D(str(r[source])) + (sde_delta[r['date']] if changed else D(0))}
            for r in rows
        ])
    ssr = pd.DataFrame([{'effective_date': date.fromisoformat(r['date']),
                         'ssr_apy': D(str(r['ssr_apy']))} for r in rows])
    rates = ReferenceRateHistory(pd.DataFrame([
        {'effective_date': date.fromisoformat(r['date']), 'ref_rate_apr': D(str(r['ref_rate_apr']))}
        for r in rows]), 'sofr')
    assert rates.at(date(2026, 9, 30)) == D('.039')
    kwargs = dict(
        psm_usds=series('psm_usds', 'cum_usds_leg'),
        curve_idle_usds=series('curve_idle', 'cum_balance'),
        lending_idle_usds=series('lending_idle', 'cum_balance'),
        basin_idle_usds=pd.DataFrame([
            {'block_date': date.fromisoformat(r['date']), 'cum_balance': D(r['basin_idle']),
             'ilk_debt': D(r['basin_ilk_debt'])} for r in rows]),
        subsidy_config=prime.subsidy, ref_rate_history=rates,
    )
    args = (pnl.period, series('cum_debt', 'cum_debt'), series('alm_usds', 'cum_balance'), ssr)
    old, old_daily, _ = compute_sky_revenue_daily(
        *args, sde_asset_value=series('sde_av', 'cum_value'), **kwargs)
    assert abs(old - (pnl.sky_revenue - pnl.sde_revenue + pnl.susds_spread_reimbursement)) < D('1e-6')
    total, daily, summary = compute_sky_revenue_daily(
        *args, sde_asset_value=series('sde_av', 'cum_value', True), **kwargs)
    serialized = []
    for before, after, saved in zip(old_daily.to_dict('records'), daily.to_dict('records'), rows, strict=True):
        assert abs(D(str(before['daily_sky_rev'])) - D(saved['daily_sky_rev'])) < D('1e-6')
        record = copy.deepcopy(saved)
        for key in ('sde_av', 'utilized', 'daily_sky_rev'):
            record[key] = str(after[key])
        # Preserve all unaffected serialized fields exactly, including float
        # rates. The old/new recomputation must agree on every other field.
        assert all(before[k] == after[k] for k in before if k not in {'sde_av', 'utilized', 'daily_sky_rev'})
        serialized.append(record)
    return total, serialized, summary


def refresh():
    source_paths = [
        DATA / 'spark-s1-treasury-events.json', DATA / 'grove-e10-capital-flows.json',
        DATA.parent / 'buidl-september-ledger-validation.json',
        ROOT / 'tests/fixtures/buidl_september_boundary.json',
        *[ROOT / 'config' / name for name in ('spark.yaml', 'grove.yaml', 'sky_direct_exposures.yaml', 'redemption_settlements.yaml')],
        *[ROOT / 'src/settle' / name for name in ('compute/prime_agent_revenue.py', 'compute/sky_revenue.py', 'normalize/redemption_settlements.py')],
    ]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
    outputs, audit = {}, {'month': '2026-09', 'api_published': False, 'source_sha256': hashes, 'venues': {}}
    for prime_id, venue_id in [('spark', 'S1'), ('grove', 'E10')]:
        saved, pnl = baseline(prime_id)
        prime = load_prime_by_id(prime_id, config_dir=ROOT / 'config')
        venue = next(v for v in prime.venues if v.id == venue_id)
        old = next(v for v in pnl.venue_breakdown if v.venue_id == venue_id)
        additional = {}
        bridge = {}
        if prime_id == 'spark':
            source = json.loads((DATA / 'spark-s1-treasury-events.json').read_text())
            assert source['pin'] == pnl.period.pin_blocks[venue.chain]
            assert source['holder'] == (venue.holder_override or prime.alm[venue.chain]).hex
            assert set(source['senders']) <= {a.hex for a in prime.external_sources_for_period(venue.chain, pnl.period.start)}
            assert len({(r['tx'], r['log_index']) for r in source['events']}) == len(source['events'])
            for r in source['events']:
                assert pnl.period.start <= date.fromisoformat(r['date']) <= pnl.period.end
                assert r['block'] <= source['pin'] and r['token'] == venue.token.address.hex
                assert r['sender'] in source['senders'] and r['recipient'] == source['holder']
            delta = sum((D(r['amount']) for r in source['events']), D(0))
            assert old.external_revenue == 0 and old.sd_share == 0
            new = replace(old, external_revenue=delta, revenue=old.revenue + delta)
        else:
            assert venue.nav_oracle.kind == 'const_one'
            assert venue.nav_haircut_bps == D(5) and venue.nav_haircut_effective_date == pnl.period.start
            factor = D('.9995')
            flow_source = json.loads((DATA / 'grove-e10-capital-flows.json').read_text())
            assert flow_source['pin'] == pnl.period.pin_blocks[venue.chain]
            raw = pd.DataFrame([{'block_date': date.fromisoformat(r['block_date']),
                                 'daily_inflow': D(r['daily_net'])} for r in flow_source['rows']])
            raw['cum_inflow'] = raw['daily_inflow'].cumsum()
            entry = load_sde_table().overlaps_venue('grove', 'E10', pnl.period.start, pnl.period.end)
            assert entry.kind == 'fixed'
            # Reproduce the original result before applying any correction.
            native = compute_venue_revenue(pnl.period, VenueRevenueInputs(
                replace(venue, nav_haircut_bps=D(0)), old.value_som, old.value_eom, raw, sde_entry=entry))
            assert canonical(native) == canonical(old)
            flows = raw.copy()
            flows['daily_inflow'] = [r['daily_inflow'] * (factor if pnl.period.start <= r['block_date'] <= pnl.period.end else D(1))
                                     for _, r in raw.iterrows()]
            flows['cum_inflow'] = flows['daily_inflow'].cumsum()
            ledger_source = json.loads((DATA.parent / 'buidl-september-ledger-validation.json').read_text())
            assert ledger_source['pin_block'] == pnl.period.pin_blocks[venue.chain]
            events = json.loads((ROOT / 'tests/fixtures/buidl_september_boundary.json').read_text())['events']
            for settlement in ledger_source['ledger']['settlements']:
                events += [settlement['cash'], *settlement['requests']]
            # Recorded ledger rows have derived fields; normalize to raw events
            # before deduplication with the separate boundary fixture.
            events = [{k: r[k] for k in ('kind', 'date', 'amount', 'tx', 'block', 'log_index', 'token', 'sender', 'recipient')}
                      for r in events]
            ledger = match_redemptions(venue, pnl.period, policy_for('grove', 'E10'), events)
            assert canonical(ledger) == ledger_source['ledger']
            marked = compute_venue_revenue(pnl.period, VenueRevenueInputs(
                venue, old.value_som, old.value_eom * factor, flows, sde_entry=entry))
            opening_mark = -old.value_som * (1 - factor)
            bridge = {
                'opening_position_markdown': opening_mark,
                'remaining_nav_flow_repricing': marked.actual_revenue - old.actual_revenue - opening_mark,
                'cash_realization': ledger.revenue_adjustment,
                'restored_small_capital_outflows': sum((D(r['carrying_value_usd']) for r in ledger.capital_outflows), D(0)),
            }
            flows = restore_redemption_capital(flows, ledger)
            new = compute_venue_revenue(pnl.period, VenueRevenueInputs(
                venue, old.value_som, old.value_eom * factor, flows, sde_entry=entry,
                redemption_revenue_adjustment=ledger.revenue_adjustment,
                redemption_settlements=list(ledger.settlements), outstanding_redemptions=list(ledger.outstanding),
                unmatched_redemption_cash=list(ledger.unmatched_cash), redemption_capital_outflows=list(ledger.capital_outflows)))
            assert sum(bridge.values(), D(0)) == new.actual_revenue - old.actual_revenue
            breakdown, deltas = [], {}
            for item in pnl.sde_daily_breakdown:
                if item.venue_id != 'E10':
                    breakdown.append(item)
                    continue
                assert len(item.daily) == 30
                daily = []
                for r in item.daily:
                    assert r['cum_value'] == r['uncapped_value']
                    day = r['block_date']
                    assert not any(w.burn_date <= day < w.usdc_settlement_date for w in entry.in_flight_redemptions)
                    deltas[day.isoformat()] = r['cum_value'] * (factor - 1)
                    daily.append({**r, 'cum_value': r['cum_value'] * factor, 'uncapped_value': r['uncapped_value'] * factor})
                assert item.daily[-1]['uncapped_value'] == old.value_eom
                breakdown.append(replace(item, daily=daily))
            interest, interest_daily, subsidy = reprice(pnl, prime, deltas)
            sde = pnl.sde_revenue + new.sd_revenue - old.sd_revenue
            additional = dict(sde_daily_breakdown=breakdown, sde_revenue=sde,
                              sky_revenue=interest + sde - pnl.susds_spread_reimbursement,
                              sky_revenue_daily=interest_daily, subsidy_summary=subsidy)
        prime_revenue = pnl.prime_agent_revenue + new.revenue - old.revenue
        sky = additional.get('sky_revenue', pnl.sky_revenue)
        result = replace(pnl, venue_breakdown=[new if v.venue_id == venue_id else v for v in pnl.venue_breakdown],
                         prime_agent_revenue=prime_revenue,
                         monthly_pnl=prime_revenue + pnl.agent_rate + pnl.distribution_rewards + pnl.chronicle_points + pnl.gar - sky,
                         **additional)
        unchanged = [v.venue_id for v in pnl.venue_breakdown if v.venue_id != venue_id]
        assert all(a == b for a, b in zip(pnl.venue_breakdown, result.venue_breakdown, strict=True) if a.venue_id != venue_id)
        outputs[prime_id] = (result, saved)
        audit['venues'][prime_id] = {
            'venue_id': venue_id, 'revenue_bridge': canonical(bridge), 'baseline_file_sha256': BASELINES[prime_id],
            'baseline_result_sha256': digest(saved['result']), 'result_sha256': digest(canonical(result)),
            'unchanged_venue_ids': unchanged, 'before': canonical(old), 'after': canonical(new),
            'prime_revenue_delta': str(result.prime_agent_revenue - pnl.prime_agent_revenue),
            'sky_claim_delta': str(result.sky_revenue - pnl.sky_revenue),
            'borrowing_cost_delta': str((result.sky_revenue - result.sde_revenue) - (pnl.sky_revenue - pnl.sde_revenue)),
            'sde_revenue_delta': str(result.sde_revenue - pnl.sde_revenue),
        }
    return outputs, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--write-reports', action='store_true')
    args = parser.parse_args()
    outputs, audit = refresh()
    args.output.mkdir(parents=True, exist_ok=True)
    for prime, (pnl, saved) in outputs.items():
        lineage = {'method': 'isolated S1/E10 September refresh; all unselected venue results reused',
                   'baseline_versions': saved['input_provenance']['versions'],
                   'baseline_file_sha256': BASELINES[prime],
                   'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   'recalculated_source_sha256': audit['source_sha256'],
                   'api_comparison': 'older API revisions have not been refreshed for these changes'}
        payload = {'result': canonical(pnl), 'input_provenance': {
            'reference_rates': saved['input_provenance']['reference_rates'], 'selective_refresh': lineage}}
        (args.output / f'{prime}.json').write_text(json.dumps(payload, indent=2) + '\n')
        if args.write_reports:
            from settle.load.writer import write_settlement
            paths = write_settlement(pnl, ROOT / f'settlements/{prime}/2026-09', sources=lineage,
                             reference_rates=saved['input_provenance']['reference_rates'])
            if 'xlsx' not in paths:
                raise RuntimeError(f'{prime}: settlement workbook was not generated')
            paths['summary'].write_text(paths['summary'].read_text().rstrip() + '\n')
    (args.output / 'audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    print(json.dumps({p: {k: v for k, v in r.items() if k.endswith('_delta')} for p, r in audit['venues'].items()}, indent=2))


if __name__ == '__main__':
    main()
