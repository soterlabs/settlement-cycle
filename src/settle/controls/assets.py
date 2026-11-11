"""Daily stock/flow controls, independent of the settlement's saved deductions.

Evidence is captured separately and reviewed under the ALM transfer workflow.
This module validates its completeness and arithmetic; it does not manufacture
an inventory, classify unknown transfers, or infer equity as a balancing plug.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from settle.domain.config import load_prime_by_id

DEDUCTIONS = ('alm_usds', 'psm_usds', 'sde_av', 'curve_idle', 'lending_idle',
              'basin_idle', 'bridge_usds')
TOLERANCE = Decimal('0.01')
ROOT = Path(__file__).resolve().parents[3]
# Asset and debt effects of externally sourced journal entries. Internal
# moves (including bridge sends/releases and vault deposits) have no effect.
EFFECTS = {
    'borrowing': (1, 1), 'repayment': (-1, -1),
    'contribution': (1, 0), 'distribution': (-1, 0),
    'income': (1, 0), 'expense': (-1, 0),
    'valuation_gain': (1, 0), 'valuation_loss': (-1, 0),
    'debt_accrual': (0, 1), 'debt_forgiveness': (0, -1),
    'internal': (0, 0),
}


class AssetControlError(ValueError):
    """Missing evidence or a failed independent settlement control."""


def require(condition, message):
    if not condition:
        raise AssetControlError(message)


def amount(value):
    require(isinstance(value, (str, int)) and not isinstance(value, bool),
            'Amounts must be explicit decimal strings or integers')
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise AssetControlError('Invalid decimal amount') from exc
    require(result.is_finite() and result >= 0, 'Amounts must be finite and non-negative')
    return result


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f'Duplicate JSON key: {key}')
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=unique)


def _evidence(root, relative):
    path = (root / relative).resolve()
    require(path.is_relative_to(root.resolve()) and path.is_file(),
            'Evidence must be a retained file inside the review directory')
    return path


def validate_controls(control, observations, journal, *, expected_days, expected_pins):
    """Pure arithmetic validation after source hashes and review are checked.

    ``expected_days`` includes the previous month's final close. Each daily
    observation must list every account and ilk, even when its value is zero.
    Deductions are per-account eligible portions, not a second list of assets.
    """
    inventory = control['inventory']
    accounts = inventory['accounts']
    require(bool(accounts), 'An empty asset inventory cannot prove coverage')
    claims, custody = set(), set()
    for key, account in accounts.items():
        require(account.get('claim_id') and account.get('custody_id'),
                f'Missing economic/physical identity for {key}')
        physical = (account['chain'], account['holder'].lower(), account['custody_id'].lower())
        require(account['claim_id'] not in claims and physical not in custody,
                f'Duplicate economic claim or custody representation: {key}')
        require(account['chain'] in inventory['chains'], f'Unknown account chain: {key}')
        claims.add(account['claim_id'])
        custody.add(physical)
        require(set(account['deduction_fields']) <= set(DEDUCTIONS), 'Unknown deduction field')
    require(len(inventory['ilks']) == len(set(inventory['ilks'])), 'Duplicate debt ilk')
    require([row['day'] for row in observations] == expected_days,
            'Independent observations must cover every opening/daily close exactly once in order')
    require([row['day'] for row in journal] == expected_days[1:],
            'Journal must explicitly cover every settlement day, including days without flows')
    require(set(expected_pins) == {expected_days[0], expected_days[-1]},
            'Both settlement boundary pins are required')
    totals, used_entries = [], set()
    previous_pins = None
    for observation in observations:
        day = observation['day']
        pins = observation['pins']
        require(set(pins) == set(inventory['chains']), f'Incomplete chain pins on {day}')
        require(all(type(pin) is int and pin > 0 for pin in pins.values()), 'Invalid block pin')
        if previous_pins is not None:
            require(all(pins[c] > previous_pins[c] for c in pins), f'Non-increasing daily pins on {day}')
        previous_pins = pins
        if day in expected_pins:
            require(pins == expected_pins[day], f'Settlement/evidence block pins differ on {day}')
        require(set(observation['assets']) == set(accounts), f'Incomplete asset inventory on {day}')
        require(set(observation['debt']) == set(inventory['ilks']), f'Incomplete debt inventory on {day}')
        assets, deductions = Decimal(0), dict.fromkeys(DEDUCTIONS, Decimal(0))
        for key, state in observation['assets'].items():
            require(bool(state['evidence']), f'Missing position evidence: {day}/{key}')
            require(state['block'] == pins[accounts[key]['chain']], f'Unpinned position: {day}/{key}')
            value = amount(state['value_usd'])
            parts = state['deductions']
            require(set(parts) == set(accounts[key]['deduction_fields']),
                    f'Missing/unsupported deduction allocation: {day}/{key}')
            eligible = {field: amount(v) for field, v in parts.items()}
            require(sum(eligible.values(), Decimal(0)) <= value + TOLERANCE,
                    f'Deductions exceed the owned asset: {day}/{key}')
            for field, v in eligible.items():
                deductions[field] += v
            assets += value
        debt = Decimal(0)
        for key, state in observation['debt'].items():
            require(bool(state['evidence']), f'Missing debt evidence: {day}/{key}')
            require(state['block'] == pins['ethereum'], f'Unpinned debt: {day}/{key}')
            debt += amount(state['value_usd'])
        totals.append({'day': day, 'assets': assets, 'debt': debt, 'deductions': deductions})
    for i, row in enumerate(journal, start=1):
        asset_change, debt_change = Decimal(0), Decimal(0)
        for entry in row['entries']:
            require(entry['id'] not in used_entries, f'Duplicate journal event: {entry["id"]}')
            used_entries.add(entry['id'])
            require(entry['kind'] in EFFECTS, 'Unknown/unexplained journal classification')
            require(entry['evidence'] and entry['rationale'].strip(), 'Unsubstantiated journal entry')
            value = amount(entry['amount_usd'])
            a, d = EFFECTS[entry['kind']]
            asset_change += a * value
            debt_change += d * value
        for field, change in (('assets', asset_change), ('debt', debt_change)):
            residual = totals[i][field] - totals[i-1][field] - change
            require(abs(residual) <= TOLERANCE,
                    f'Unexplained {field} movement on {row["day"]}: {residual}')
    return totals[1:]


def validate_packet(path, pnl, *, repo_root=ROOT):
    """Validate retained independent evidence, then compare every MSC day.

    The reviewed inventory and all raw/source/observation/journal artifacts
    are hash-bound to the ALM review. An attest-only 'passed' flag is not used.
    """
    path = Path(path)
    require(path.is_file(), f'Missing independent asset controls: {path}')
    root = path.parent
    packet_hash = sha256(path)
    control = _json(path)
    review_path = root / 'review.json'
    require(review_path.is_file(), 'Missing reviewed ALM transfer inventory')
    review_hash = sha256(review_path)
    review = _json(review_path)
    month = str(pnl.month)
    require(pnl.period.start == pnl.month.first_day and pnl.period.end == pnl.month.last_day,
            'Canonical asset controls require a complete calendar month')
    for record in (control, review):
        require(record['prime'] == pnl.prime_id and record['month'] == month, 'Wrong prime/month')
    require(control['schema'] == 1, 'Unsupported asset control schema')
    require(review['status'] == 'reviewed' and review['unclassified_count'] == 0
            and review['reviewer'].strip() and review['reviewed_at'].strip(),
            'A complete reviewed transfer inventory is required')
    require(datetime.fromisoformat(review['reviewed_at']).tzinfo is not None, 'Review time must include timezone')
    config = repo_root / 'config' / f'{pnl.prime_id}.yaml'
    config_hash = sha256(config)
    require(control['config_sha256'] == review['config_sha256'] == config_hash,
            'Asset controls/transfer review use a stale configuration')
    inventory = control['inventory']
    require(review['asset_inventory_sha256'] == canonical_hash(inventory),
            'Asset inventory has not been reviewed')
    require(control['evidence_sha256'] == review['evidence_sha256']
            and bool(control['evidence_sha256']), 'Incomplete reviewed evidence manifest')
    evidence = {}
    for relative, digest in control['evidence_sha256'].items():
        require(relative not in {path.name, 'review.json'}, 'Evidence cannot reference its own approval')
        source = _evidence(root, relative)
        require(sha256(source) == digest, f'Changed evidence: {relative}')
        if source.suffix == '.json':
            evidence[relative] = _json(source)
    for field in ('observations', 'journal', 'bridge_evidence'):
        require(control[field] in control['evidence_sha256'], f'Unreviewed {field}')
    prime = load_prime_by_id(pnl.prime_id, config_dir=repo_root / 'config')
    # Include every configured wallet/venue in the review. Report-only or
    # non-beneficial positions require an explicit reviewed exclusion.
    active_alm = {chain: holder for chain, holder in prime.alm.items()
                  if prime.alm_start_dates.get(chain, pnl.period.end) <= pnl.period.end}
    wallets = {f'{chain.value}:{str(holder).lower()}' for group in (active_alm, prime.subproxy)
               for chain, holder in group.items()}
    require(wallets <= set(inventory['wallets']), 'Configured ALM/SubProxy wallet omitted from inventory')
    require(set(inventory['venues']) == {v.id for v in prime.venues}, 'Configured venue inventory is incomplete')
    for disposition in inventory['venues'].values():
        require(bool(disposition['accounts']) != bool(disposition.get('exclusion')),
                'Every venue needs owned accounts or a reviewed exclusion, exclusively')
        require(set(disposition['accounts']) <= set(inventory['accounts']), 'Unknown venue account')
        if disposition.get('exclusion'):
            exclusion = disposition['exclusion']
            require(exclusion['kind'] in {'not_owned', 'outside_period', 'outside_msc'}
                    and exclusion['rationale'].strip() and exclusion['evidence']
                    and set(exclusion['evidence']) <= set(control['evidence_sha256'])
                    - {control['observations'], control['journal']}, 'Unsubstantiated venue exclusion')
    for account in inventory['accounts'].values():
        require(f'{account["chain"]}:{account["holder"].lower()}' in inventory['wallets'],
                'Unreviewed asset holder')
    ilks = [prime.ilk_bytes32, *prime.extra_ilks]
    require(set(inventory['ilks']) == {'0x' + ilk.hex() for ilk in ilks if ilk is not None},
            'Configured debt ilk omitted from inventory')
    opening = pnl.period.start - timedelta(days=1)
    days = [str(opening + timedelta(days=i)) for i in range(pnl.period.n_days + 1)]
    observations = evidence[control['observations']]
    journal = evidence[control['journal']]
    # Every individual observation/journal references retained raw inputs.
    refs = [s['evidence'] for row in observations for section in ('assets', 'debt')
            for s in row[section].values()]
    refs += [e['evidence'] for row in journal for e in row['entries']]
    for references in refs:
        require(isinstance(references, list) and bool(references), 'Missing raw input references')
        require(set(references) <= set(control['evidence_sha256'])
                - {control['observations'], control['journal']}, 'Unretained or circular source evidence')
    pins = {
        days[0]: {c.value: n for c, n in pnl.pin_blocks_som.items()},
        days[-1]: {c.value: n for c, n in pnl.period.pin_blocks.items()},
    }
    require(set(inventory['chains']) == {c.value for c in prime.chains_at(pnl.period.end)},
            'Configured chain omitted from control inventory')
    require(set(inventory['chains']) == set(pins[days[0]]) == set(pins[days[-1]]),
            'Settlement chain coverage is incomplete')
    coverage = review['scan_coverage']
    require(set(coverage) == set(inventory['wallets']), 'Incomplete transfer scan wallet coverage')
    for identity, scan in coverage.items():
        chain = identity.split(':', 1)[0]
        require(scan['status'] == 'complete' and scan['unclassified_count'] == 0
                and scan['classified_transfer_count'] == scan['transfer_count']
                and type(scan['transfer_count']) is int and scan['transfer_count'] >= 0,
                f'Incomplete reviewed transfer scan: {identity}')
        require(set(scan['token_standards']) == {'erc20', 'erc721', 'native'},
                f'Incomplete token scan scope: {identity}')
        require(scan['from_block'] <= pins[days[0]][chain] + 1
                and scan['to_block'] >= pins[days[-1]][chain],
                f'Transfer scan does not cover the month: {identity}')
        require(bool(scan['evidence']) and set(scan['evidence']) <= set(control['evidence_sha256'])
                - {control['observations'], control['journal']}, f'Unretained transfer scan: {identity}')
    totals = validate_controls(control, observations, journal, expected_days=days, expected_pins=pins)
    from settle.controls.wallets import check_wallet_states
    check_wallet_states(prime, observations, control, evidence)
    from settle.controls.bridges import check_bridge_states
    check_bridge_states(prime, pnl.period, observations, control, evidence[control['bridge_evidence']])
    require([str(row['date']) for row in pnl.sky_revenue_daily] == days[1:],
            'Settlement daily coverage is incomplete')
    for observed, reported in zip(totals, pnl.sky_revenue_daily, strict=True):
        values = {'cum_debt': observed['debt'], **observed['deductions']}
        for field, expected in values.items():
            actual = amount(reported.get(field, '0'))
            require(abs(actual - expected) <= TOLERANCE,
                    f'Daily holdings disagree with {field} on {observed["day"]}: '
                    f'MSC={actual}, independently observed={expected}')
    require(sha256(path) == packet_hash and sha256(review_path) == review_hash, 'Review changed during validation')
    for relative, digest in control['evidence_sha256'].items():
        require(sha256(_evidence(root, relative)) == digest, f'Evidence changed during validation: {relative}')
    return {'schema': 1, 'status': 'passed', 'prime': pnl.prime_id, 'month': month,
            'packet_sha256': packet_hash, 'review_sha256': review_hash,
            'config_sha256': config_hash, 'evidence_sha256': control['evidence_sha256'],
            'inventory_sha256': canonical_hash(inventory), 'days_checked': len(totals),
            'tolerance_usd': str(TOLERANCE), 'opening_assets_usd': str(sum(
                (amount(s['value_usd']) for s in observations[0]['assets'].values()), Decimal(0))),
            'closing_assets_usd': str(totals[-1]['assets']),
            'daily': [{**{k: str(row[k]) for k in ('day', 'assets', 'debt')},
                       'deductions': {k: str(v) for k, v in row['deductions'].items()}} for row in totals]}


def require_asset_controls(pnl, *, repo_root=ROOT):
    path = repo_root / '.msc-transfer-reviews' / pnl.prime_id / str(pnl.month) / 'asset-controls.json'
    try:
        return validate_packet(path, pnl, repo_root=repo_root)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AssetControlError(f'Malformed/incomplete asset control evidence: {type(exc).__name__}') from exc
