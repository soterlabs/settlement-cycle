"""Monthly Grove archive income and non-additive Merkl accrual scenarios.

Receipt recognition matches existing lending/issuer reporting. Scenarios are
never an additional income stream. Old source snapshots remain independently
replayable; no daily calculation, provider calls or MSC writes occur here.
"""
from __future__ import annotations

import calendar
import csv
import hashlib
import io
import json
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext
from functools import lru_cache
from importlib.resources import files

from .financial import metric, number
from .financial_offchain import extend as offchain_extend
from .financial_offchain import publications as offchain_publications
from .verification import digest

SNAPSHOTS = ('46267e32e34dcd77f341205fe7a2928e36ca4611872894d00b6d8536e879271a',)
SPECS = {
    'monad': ('investment.morpho.grove_monad_ausd', 'Monad AUSD vault return', 'Morpho vaults'),
    'agora': ('rewards.issuer.agora', 'Agora incentive receipts', 'Issuer compensation'),
    'usdg': ('rewards.issuer.usdg', 'USDG incentive receipts', 'Issuer compensation'),
    'plume_partnership': ('rewards.partnership.plume', 'Plume partnership payment', 'Partnership income'),
    'plume_disposal': ('gains.plume_disposal', 'PLUME disposal gain', 'Token disposals'),
    'merkl_core': ('rewards.lending.aave.eth_rlusd', 'Aave Core Merkl distributions received', 'Received lending rewards'),
    'merkl_horizon': ('rewards.lending.aave.horizon_rlusd', 'Aave Horizon Merkl distributions received', 'Received lending rewards'),
}
NEW_KEYS = {'agora', 'usdg', 'plume_partnership', 'plume_disposal'}
FILES = {
    'monad-vault-monthly.csv', 'agora-monthly-cash.csv', 'usdg-monthly-rewards.csv',
    'other-income-treatment.csv', 'merkl-claims.csv', 'merkl-claims-coverage.csv',
    'merkl-current-cumulative-tie.csv', 'merkl-valuation-policy-evidence.csv',
    'merkl-monthly-weighted-illustrations.csv', 'merkl-monthly-recorded.csv',
}


def taxonomy(prime):
    return [{'id': SPECS[key][0], 'label': SPECS[key][1], 'family': SPECS[key][2], 'aggregation': 'leaf'}
            for key in SPECS if key in NEW_KEYS] if prime == 'grove' else []


def parse(snapshot):
    if snapshot['schema_version'] != 'grove-archive-income.v1':
        raise ValueError('Invalid archive schema')
    sources = {}
    for source in snapshot['sources']:
        name = source['path'].removeprefix('data/')
        if (source['path'] != 'data/' + name or name not in FILES or name in sources
                or hashlib.sha256(source['text'].encode()).hexdigest() != source['sha256']):
            raise ValueError('Invalid archive source identity/hash')
        sources[name] = source
    if set(sources) != FILES:
        raise ValueError('Incomplete archive source bundle')
    def rows(name):
        return list(csv.DictReader(io.StringIO(sources[name]['text'])))
    grouped, seen = {}, set()

    def add(component, month, value, filename, raw, *, recognition='receipt', quality='supported',
            currency='USD', basis='supplied_receipt_history', partial=False, included=True, suffix='', label=None):
        start = date.fromisoformat(month + '-01')
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        identity = component + suffix
        if (identity, month) in seen or not month.startswith('2026-'):
            raise ValueError('Duplicate/out-of-scope archive month')
        seen.add((identity, month))
        amount = number(value)
        # A confirmed zero receipt needs no FX estimate; missing entitlement
        # is still unknown, but a nominal price cannot change zero received.
        if amount == 0 and recognition == 'receipt':
            quality = 'supported'
        cell = metric(amount, basis=basis, source_paths=['data/' + filename + '#' + month + '.' + identity])
        cell.update(recognition=recognition, quality=quality, period_start=str(start), period_end=str(end),
                    source_currency=currency, conversion='supplied_USD_amount' if currency == 'USD' else 'supplied_unit_price_1_USD')
        if partial:
            cell.update(value=None, known_value=str(amount), status='partial', reason='scoped_receipts_not_complete_earned_income')
        definition = SPECS.get(component)
        grouped.setdefault(month, []).append({'id': identity, 'line_id': definition[0] if definition and included else None,
            'label': label or definition[1], 'metric': cell, 'included_in_totals': included,
            'source_file': filename, 'raw': raw})

    for raw in rows('monad-vault-monthly.csv'):
        if not raw['month'].startswith('2026-'):
            continue
        with localcontext() as ctx:
            ctx.prec = 100
            delta = (number(raw['closingVaultAssetValueAusd']) - number(raw['openingVaultAssetValueAusd'])
                     - number(raw['depositedAusd']) + number(raw['withdrawnAusd']) - number(raw['vaultReturnAusd']))
        if raw['currency'] != 'AUSD' or abs(delta) > Decimal('1e-18'):
            raise ValueError('Unreconciled Monad vault return')
        add('monad', raw['month'], raw['vaultReturnAusd'], 'monad-vault-monthly.csv', raw,
            recognition='investment_return', currency='AUSD', quality='estimate',
            basis='net_vault_return_after_capital_flows_AUSD_at_USD_par')
    for raw in rows('agora-monthly-cash.csv'):
        if (number(raw['apiUSD']) != number(raw['soterE38RecognizedUSD'])
                or number(raw['apiMinusE38USD']) != 0 or raw['accruedEntitlementUSD']
                or number(raw['nativeAUSD']) != number(raw['apiUSD'])):
            raise ValueError('Unreconciled Agora cash/unsupported accrual')
        add('agora', raw['month'], raw['apiUSD'], 'agora-monthly-cash.csv', raw,
            currency='AUSD', quality='estimate', basis='supplied_E38_receipts_AUSD_at_USD_par_not_make_whole_accrual')
    for raw in rows('usdg-monthly-rewards.csv'):
        if (raw['currency'] != 'USDG' or number(raw['rewardNativeExact']) != number(raw['nominalRewardUsd'])
                or raw['underlyingEarningPeriodVerified'] != 'False'):
            raise ValueError('Unsupported USDG reward basis')
        add('usdg', raw['month'], raw['nominalRewardUsd'], 'usdg-monthly-rewards.csv', raw,
            currency='USDG', quality='estimate', basis='nominal_USDG_receipts_at_USD_par_earning_period_unverified')
    treatments = {
        'Plume partnership cash': ('plume_partnership', '2026-04', '753649'),
        'PLUME token disposal proceeds': ('plume_disposal', '2026-06', '1102056.359999'),
    }
    for raw in rows('other-income-treatment.csv'):
        if raw['stream'] not in treatments:
            continue  # USDG summary is not an additional income stream.
        component, month, amount = treatments[raw['stream']]
        if raw['period'] != month or raw['currency'] != 'USD' or raw['amount'] != amount:
            raise ValueError('Unreviewed Plume treatment')
        add(component, month, amount, 'other-income-treatment.csv', raw,
            recognition='realized_gain' if component == 'plume_disposal' else 'receipt',
            basis='supplied_approved_zero_cost_basis_disposal' if component == 'plume_disposal' else 'supplied_partnership_cash')

    # Verify the six source events against the independent coverage/tie tables.
    coverage_rows = rows('merkl-claims-coverage.csv')
    coverage = {(r['transactionHash'], r['logIndex']): r for r in coverage_rows}
    if len(coverage) != len(coverage_rows):
        raise ValueError('Duplicate Merkl coverage proof')
    claims, totals, claim_ids = {}, {}, set()
    expected = {'aEthRLUSD': 'merkl_core', 'aHorRwaRLUSD': 'merkl_horizon'}
    tokens = {'aEthRLUSD': {'0x72eeed8043dcce2fe7cdac950d928f80f472ab80': '0xfa82580c16a31d0c1bc632a36f82e83efef3eec0'},
        'aHorRwaRLUSD': {'0x503d751b13a71d8e69db021df110bfa7ae1da889': '0xe3190143eb552456f88464662f0c0c4ac67a77eb',
                        '0xe3190143eb552456f88464662f0c0c4ac67a77eb': '0xe3190143eb552456f88464662f0c0c4ac67a77eb'}}
    for raw in rows('merkl-claims.csv'):
        key = raw['txHash'], raw['claimLogIndex']
        matched = coverage.get(key)
        amount = number(raw['exactNativeAmount'])
        if (key in claim_ids or not matched or amount <= 0 or raw['assetSymbol'] not in expected
                or tokens.get(raw['assetSymbol'], {}).get(raw['claimedToken']) != raw['receivedToken']
                or matched['date'] != raw['date'] or not raw['timestampUtc'].startswith(raw['date'])
                or matched['historyEndpointMatched'] != 'True' or matched['onchainCumulativeAmountMatched'] != 'True'
                or matched['nativeAmountExact'] != raw['exactNativeAmount']
                or matched['claimedToken'] != raw['claimedToken'] or matched['receivedToken'] != raw['receivedToken']
                or raw['beneficiary'] != '0x491edfb0b8b608044e227225c715981a30f3a44e'
                or raw['actualTokenRecipient'] != raw['beneficiary']):
            raise ValueError('Unverified/duplicate Merkl receipt')
        claim_ids.add(key)
        with localcontext() as ctx:
            ctx.prec = 100
            totals[raw['claimedToken']] = totals.get(raw['claimedToken'], Decimal(0)) + amount
        claims.setdefault((expected[raw['assetSymbol']], raw['date'][:7], raw['assetSymbol']), []).append(raw)
    if len(claim_ids) != 6 or len(coverage) != 6:
        raise ValueError('Incomplete Merkl proof scope')
    for raw in rows('merkl-current-cumulative-tie.csv'):
        if (raw['amountScopeResolved'] != 'True' or number(raw['unexplainedClaimedNativeExact']) != 0
                or number(raw['currentOnchainClaimedNativeExact']) != totals.get(raw['tokenAddress'], Decimal(0))):
            raise ValueError('Merkl current claimed-state tie mismatch')
    marks = {r['assetSymbol']: r for r in rows('merkl-valuation-policy-evidence.csv')}
    for (component, month, symbol), events in claims.items():
        mark = marks[symbol]
        if mark['observedPriceUsd'] != '1' or mark['from'] > month + '-01' or mark['through'][:7] < month:
            raise ValueError('Merkl unit-price policy missing/outside coverage')
        with localcontext() as ctx:
            ctx.prec = 100
            value = sum((number(r['exactNativeAmount']) for r in events), Decimal(0))
        add(component, month, str(value), 'merkl-claims.csv', events, currency=symbol,
            recognition='receipt_in_kind', quality='estimate', partial=True,
            basis='verified_in_kind_distributions_at_supplied_unit_price_not_earned_accrual')
    # A single, coherent provided scenario. Never add it to recorded changes,
    # received claims or the existing June catch-up as another income stream.
    for raw in rows('merkl-monthly-weighted-illustrations.csv'):
        if raw['period'] not in ('2026-04', '2026-05', '2026-06'):
            continue
        add('merkl_horizon', raw['period'], raw['balance_reward_over_tvl_eodIllustrationNative'],
            'merkl-monthly-weighted-illustrations.csv', raw, currency='aHorRwaRLUSD',
            quality='estimate', recognition='estimated_accrual', included=False, suffix='_scenario',
            basis='supplied_reward_TVL_times_EOD_balance_scenario_excluded_from_totals',
            label='Merkl Horizon earned-income scenario')
    # Recorded cumulative changes are useful evidence but are not another
    # independent earnings series. Preserve nonzero records as reference only.
    for raw in rows('merkl-monthly-recorded.csv'):
        if not raw['earnedIncome'] or number(raw['earnedIncome']) == 0:
            continue
        component = 'merkl_core' if raw['position'] == 'Aave Core RLUSD Merkl' else 'merkl_horizon'
        add(component, raw['month'], raw['earnedIncome'], 'merkl-monthly-recorded.csv', raw,
            currency=raw['currency'], quality='estimate', recognition='recorded_change', included=False,
            suffix='_recorded', basis='recorded_cumulative_change_not_economic_cutoff_excluded_from_totals',
            label='Merkl ' + ('Core' if component == 'merkl_core' else 'Horizon') + ' recorded reward changes')

    base_retained, _ = offchain_publications()
    reports = []
    for month in sorted(set(snapshot['base_revisions']) | set(grouped)):
        base_ref = snapshot['base_revisions'].get(month)
        base = deepcopy(base_retained[base_ref]) if base_ref else None
        if base and base['month'] != month:
            raise ValueError('Archive base revision/month mismatch')
        additions = grouped.get(month, [])
        if not additions:
            reports.append(base)
            continue
        source = {'kind': 'counterparty_monthly_archive', 'cutoff': max(row['metric']['period_end'] for row in additions),
            'currency': 'USD', 'archive_sha256': snapshot['archive_sha256'], 'canonical_snapshot_hash': digest(snapshot),
            'received_at': snapshot['received_at'], 'base_revision': base_ref,
            'result_hash': digest([row['raw'] for row in additions]),
            'files': (base['source']['files'] if base else []) + [{'path': s['path'], 'sha256': s['sha256']} for s in sources.values()],
            'limitations': ['Native AUSD/USDG and aToken amounts use the disclosed supplied USD par/unit-price proxy',
                'Merkl in-kind receipts are scoped known amounts, not complete earned income or bank cash',
                'Merkl recorded changes and accrual scenarios are excluded from totals',
                'Agora receipts do not establish make-whole entitlement or receivables',
                'No extrapolation beyond supplied months; MSC settlement remains unchanged'] + (base['source']['limitations'] if base else [])}
        ref = digest(source)
        source['revision_id'] = ref
        metrics = base['metrics'] if base else {}
        positions = base['positions'] if base else []
        for cell in metrics.values():
            cell['source_ref'] = ref
        for position in positions:
            for cell in position['metrics'].values():
                cell['source_ref'] = ref
        archive = []
        for row in additions:
            cell = {**row['metric'], 'source_ref': ref}
            if row['included_in_totals']:
                metrics[row['line_id']] = cell
            published = {key: row[key] for key in ('id', 'line_id', 'label', 'included_in_totals')} | {'month': month, 'metric': cell}
            if row['id'] == 'monad':
                published['balances'] = {}
                for key, field in [('opening_value', 'openingVaultAssetValueAusd'), ('closing_value', 'closingVaultAssetValueAusd'),
                                   ('deposits', 'depositedAusd'), ('withdrawals', 'withdrawnAusd')]:
                    published['balances'][key] = {**cell, 'value': row['raw'][field], 'recognition': 'valuation' if 'value' in key else 'capital_flow',
                        'basis': 'supplied_vault_asset_value_AUSD_at_USD_par' if 'value' in key else 'supplied_vault_capital_flow_AUSD_at_USD_par',
                        'source_paths': ['data/monad-vault-monthly.csv#' + month + '.' + field]}
            archive.append(published)
        reports.append({'prime': 'grove', 'month': month, 'cutoff': source['cutoff'], 'revision_id': ref,
                        'source': source, 'metrics': metrics, 'positions': positions, 'archive_income': archive})
    return reports


@lru_cache(maxsize=1)
def publications():
    retained, _ = offchain_publications()
    retained, active = dict(retained), {}
    for name in SNAPSHOTS:
        raw = files('settle.revenue').joinpath('published_archive', name + '.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != name:
            raise ValueError('Archive bundle hash mismatch')
        heads = {}
        for report in parse(json.loads(raw)):
            retained[report['revision_id']] = report
            heads[(report['prime'], report['month'])] = report['revision_id']
        active = heads
    return retained, active


def select(prime, year, as_of, selection=None):
    retained, active = publications()
    chosen = []
    for month in range(1, 13):
        key = f'{year}-{month:02}'
        ref = selection['offchain_revisions'][month - 1] if selection else active.get((prime, key))
        report = retained.get(ref) if ref else None
        if ref and (not report or report['prime'] != prime or report['month'] != key):
            raise ValueError('Retained archive revision unavailable')
        if report and date.fromisoformat(report['cutoff']) > as_of:
            if selection:
                raise ValueError('Archive revision exceeds selected cutoff')
            report = None
        chosen.append(report)
    return chosen


def extend(statement, chosen):
    offchain_extend(statement, chosen)
    statement['archive_income'] = [deepcopy(row) for report in chosen if report for row in report.get('archive_income', [])]
