"""Independent holdings and stock/flow checks; no live monthly calculation."""
import json
from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from settle.controls import assets, bridges, wallets
from settle.domain.period import Month, Period
from settle.domain.primes import Address, Chain, Prime
from settle.domain.sky_tokens import USDS_BY_CHAIN
from settle.load import writer

MONTH = Month(2026, 7)
ETH = Chain.ETHEREUM
HOLDER = Address.from_str('0x' + '11' * 20)
ILK = b'x' * 32
TOTAL = D('436845703.611569')
BRIDGE = D('346845703.611569')


def headers(day, block):
    stamp = int(datetime.combine(date.fromisoformat(day), time.max, UTC).timestamp())
    return [{'number': hex(block), 'hash': '0x' + f'{block:064x}', 'timestamp': hex(stamp - 5)},
            {'number': hex(block+1), 'hash': '0x' + f'{block+1:064x}',
             'parentHash': '0x' + f'{block:064x}', 'timestamp': hex(stamp + 7)}]


def dataset():
    prime = Prime('spark', ILK, date(2025, 1, 1), alm={ETH: HOLDER})
    opening = MONTH.first_day - timedelta(days=1)
    days = [str(opening + timedelta(days=i)) for i in range(32)]
    accounts = {
        'wallet': {'claim_id': 'raw-wallet-usds', 'custody_id': USDS_BY_CHAIN[ETH].address.hex,
                   'chain': 'ethereum', 'holder': HOLDER.hex, 'deduction_fields': ['alm_usds']},
        'bridge': {'claim_id': 'bridge-claim', 'custody_id': 'gateway/claim',
                   'chain': 'ethereum', 'holder': HOLDER.hex, 'deduction_fields': ['bridge_usds']},
        'destination': {'claim_id': 'destination-position', 'custody_id': 'destination-receipt',
                        'chain': 'ethereum', 'holder': HOLDER.hex, 'deduction_fields': []},
    }
    control = {'schema': 1, 'prime': 'spark', 'month': str(MONTH),
               'inventory': {'accounts': accounts, 'ilks': ['0x' + ILK.hex()],
                             'wallets': [f'ethereum:{HOLDER.hex}'], 'venues': {}, 'chains': ['ethereum']},
               'observations': 'observations.json', 'journal': 'journal.json'}
    observations, raw, reported = [], [], []
    for i, day in enumerate(days):
        wallet = TOTAL if i < 6 else D('90000000')
        bridge = BRIDGE if 6 <= i <= 12 else D(0)
        destination = BRIDGE if i > 12 else D(0)
        pin = 1000 + 100 * i
        def state(value, deductions, pin=pin):
            return {'value_usd': str(value), 'deductions': deductions, 'block': pin,
                    'evidence': ['wallets.json'] if 'alm_usds' in deductions else ['raw.json']}
        observations.append({'day': day, 'pins': {'ethereum': pin},
                             'assets': {'wallet': state(wallet, {'alm_usds': str(wallet)}),
                                        'bridge': state(bridge, {'bridge_usds': str(bridge)}),
                                        'destination': state(destination, {})},
                             'debt': {'0x' + ILK.hex(): state(TOTAL, {})}})
        raw.append({'day': day, 'chain': 'ethereum', 'holder': HOLDER.hex,
                    'token': USDS_BY_CHAIN[ETH].address.hex, 'block': pin,
                    'raw_balance': str(int(wallet * 10**18)), 'decimals': 18,
                    'headers': headers(day, pin), 'chain_id': 1, 'head': 100000})
        if i:
            reported.append({'date': day, 'cum_debt': str(TOTAL),
                             **{field: '0' for field in assets.DEDUCTIONS},
                             'alm_usds': str(wallet), 'bridge_usds': str(bridge)})
    journal = [{'day': day, 'entries': []} for day in days[1:]]
    pnl = SimpleNamespace(prime_id='spark', month=MONTH,
                          period=Period.from_month(MONTH, {ETH: 4100}),
                          pin_blocks_som={ETH: 1000}, sky_revenue_daily=reported,
                          is_provisional=False)
    source = {'schema': 1, 'source': 'independent-archive-usds', 'prime': 'spark', 'states': raw}
    return prime, control, observations, journal, pnl, source


def validate(control, observations, journal):
    return assets.validate_controls(control, observations, journal,
                                    expected_days=[r['day'] for r in dataset()[2]],
                                    expected_pins={'2026-06-30': {'ethereum': 1000},
                                                   '2026-07-31': {'ethereum': 4100}})


def write_packet(tmp_path, monkeypatch):
    prime, control, observations, journal, pnl, source = dataset()
    monkeypatch.setattr(assets, 'load_prime_by_id', lambda *a, **kw: prime)
    # Packet fixture is a one-chain allocation, not an authenticated bridge.
    # The stock/flow unit tests above use a synthetic bridge separately.
    control['inventory']['accounts'].pop('bridge')
    for observation in observations:
        observation['assets'].pop('bridge')
        wallet = D(observation['assets']['wallet']['value_usd'])
        observation['assets']['destination']['value_usd'] = str(TOTAL - wallet)
    for row in pnl.sky_revenue_daily:
        row['bridge_usds'] = '0'
    control['bridge_evidence'] = 'gateways.json'
    root = tmp_path / '.msc-transfer-reviews/spark/2026-07'
    root.mkdir(parents=True)
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/spark.yaml').write_text('synthetic test configuration, not a real review')
    for name, value in [('observations.json', observations), ('journal.json', journal),
                        ('wallets.json', source), ('gateways.json', bridges.capture_bridges(prime, pnl.period)),
                        ('raw.json', {'synthetic': True})]:
        (root / name).write_text(json.dumps(value))
    control['evidence_sha256'] = {p.name: assets.sha256(p) for p in root.iterdir()}
    control['config_sha256'] = assets.sha256(tmp_path / 'config/spark.yaml')
    review = {'prime': 'spark', 'month': '2026-07', 'status': 'reviewed',
              'reviewer': 'synthetic test fixture only', 'reviewed_at': '2026-08-01T00:00:00Z',
              'unclassified_count': 0, 'config_sha256': control['config_sha256'],
              'evidence_sha256': control['evidence_sha256'],
              'asset_inventory_sha256': assets.canonical_hash(control['inventory']),
              'scan_coverage': {f'ethereum:{HOLDER.hex}': {
                  'status': 'complete', 'unclassified_count': 0, 'transfer_count': 0,
                  'classified_transfer_count': 0, 'token_standards': ['erc20', 'erc721', 'native'],
                  'from_block': 1001, 'to_block': 4100, 'evidence': ['raw.json']}}}
    (root / 'review.json').write_text(json.dumps(review))
    path = root / 'asset-controls.json'
    path.write_text(json.dumps(control))
    return path, pnl


def test_internal_bridge_send_and_release_preserve_total_assets():
    _, control, observations, journal, _, _ = dataset()
    rows = validate(control, observations, journal)
    assert {r['assets'] for r in rows} == {TOTAL}
    assert rows[5]['deductions']['bridge_usds'] == BRIDGE
    assert rows[12]['deductions']['bridge_usds'] == 0


@pytest.mark.parametrize('change,match', [
    (lambda c, o, j: o.pop(13), 'every opening/daily close'),
    (lambda c, o, j: o[13]['assets'].pop('destination'), 'Incomplete asset inventory'),
    (lambda c, o, j: o[13]['debt'].clear(), 'Incomplete debt inventory'),
    (lambda c, o, j: o[13]['assets']['bridge'].update(value_usd=str(BRIDGE)), 'Unexplained assets'),
    (lambda c, o, j: o[6]['assets']['wallet']['deductions'].update(alm_usds=str(TOTAL)), 'exceed the owned asset'),
    (lambda c, o, j: c['inventory']['accounts']['destination'].update(claim_id='bridge-claim'), 'Duplicate economic'),
    (lambda c, o, j: c['inventory']['accounts']['destination'].update(custody_id='gateway/claim'), 'Duplicate economic'),
    (lambda c, o, j: o[8]['assets']['wallet'].update(value_usd='NaN'), 'finite'),
    (lambda c, o, j: o[8]['assets']['wallet'].update(value_usd=90.0), 'decimal strings'),
    (lambda c, o, j: o[8]['assets']['wallet'].update(evidence=[]), 'Missing position evidence'),
    (lambda c, o, j: o[8]['assets']['wallet'].update(block=1), 'Unpinned'),
    (lambda c, o, j: j.pop(), 'Journal must explicitly'),
    (lambda c, o, j: j[0]['entries'].append({'id': 'plug', 'kind': 'residual'}), 'Unknown/unexplained'),
])
def test_incomplete_or_inconsistent_proof_fails(change, match):
    _, control, observations, journal, _, _ = dataset()
    change(control, observations, journal)
    with pytest.raises(assets.AssetControlError, match=match):
        validate(control, observations, journal)


def test_debt_and_asset_rollforward_are_separate_and_do_not_assume_equity_zero():
    _, control, observations, journal, _, _ = dataset()
    for row in observations:
        row['debt']['0x' + ILK.hex()]['value_usd'] = '10'
    assert validate(control, observations, journal)[0]['debt'] == 10
    observations[-1]['debt']['0x' + ILK.hex()]['value_usd'] = '11'
    with pytest.raises(assets.AssetControlError, match='Unexplained debt'):
        validate(control, observations, journal)
    journal[-1]['entries'] = [{'id': 'accrual', 'kind': 'debt_accrual', 'amount_usd': '1',
                              'evidence': ['raw.json'], 'rationale': 'independent rate-scaled Vat observation'}]
    assert validate(control, observations, journal)[-1]['debt'] == 11


def test_reviewed_packet_checks_all_days_and_retains_hashes(tmp_path, monkeypatch):
    path, pnl = write_packet(tmp_path, monkeypatch)
    result = assets.validate_packet(path, pnl, repo_root=tmp_path)
    assert result['days_checked'] == 31
    assert result['packet_sha256'] == assets.sha256(path)
    assert result['daily'][12]['assets'] == str(TOTAL)


@pytest.mark.parametrize('day', ['2026-07-06', '2026-07-13', '2026-07-30'])
def test_july_stale_idle_is_detected_even_though_month_end_is_correct(tmp_path, monkeypatch, day):
    path, pnl = write_packet(tmp_path, monkeypatch)
    row = next(r for r in pnl.sky_revenue_daily if r['date'] == day)
    row['alm_usds'] = str(TOTAL)
    with pytest.raises(assets.AssetControlError, match=f'alm_usds on {day}'):
        assets.validate_packet(path, pnl, repo_root=tmp_path)


def test_copied_stale_observation_cannot_pass_independent_raw_wallet_check():
    prime, control, observations, _, _, raw = dataset()
    observations[13]['assets']['wallet'].update(value_usd=str(TOTAL), deductions={'alm_usds': str(TOTAL)})
    with pytest.raises(assets.AssetControlError, match='Stale wallet value/deduction'):
        wallets.check_wallet_states(prime, observations, control, {'wallets.json': raw, 'raw.json': {}})


@pytest.mark.parametrize('target', ['raw.json', 'observations.json', 'journal.json'])
def test_changed_evidence_invalidates_review(tmp_path, monkeypatch, target):
    path, pnl = write_packet(tmp_path, monkeypatch)
    (path.parent / target).write_text('{}')
    with pytest.raises(assets.AssetControlError, match='Changed evidence'):
        assets.validate_packet(path, pnl, repo_root=tmp_path)


def test_missing_proof_blocks_writer_before_creating_output(tmp_path, monkeypatch):
    _, _, _, _, pnl, _ = dataset()
    monkeypatch.setattr(writer, '_REPO_ROOT', tmp_path)
    destination = tmp_path / 'output'
    with pytest.raises(assets.AssetControlError, match='Missing independent asset controls'):
        writer.write_settlement(pnl, destination, allow_overwrite=True)
    assert not destination.exists()


def test_canonical_writer_retains_control_result(tmp_path, monkeypatch):
    _, pnl = write_packet(tmp_path, monkeypatch)
    monkeypatch.setattr(writer, '_REPO_ROOT', tmp_path)
    seen = {}
    def render(pnl, output_dir, **kwargs):
        seen.update(kwargs)
        return {'test': output_dir}
    monkeypatch.setattr(writer, '_write_settlement_artifacts', render)
    writer.write_settlement(pnl, tmp_path / 'output')
    assert seen['asset_controls']['days_checked'] == 31


def test_archive_capture_uses_raw_state_at_canonical_block_hash():
    prime, _, observations, _, _, _ = dataset()
    day = observations[6]['day']
    pin = observations[6]['pins']['ethereum']
    captured = []
    def call(chain, method, params):
        captured.append((method, params))
        if method == 'eth_chainId':
            return '0x1'
        if method == 'eth_blockNumber':
            return hex(100000)
        if method == 'eth_getBlockByNumber':
            return headers(day, pin)[int(params[0], 16) - pin]
        assert params[1] == {'blockHash': headers(day, pin)[0]['hash'], 'requireCanonical': True}
        return '0x' + f'{90_000_000 * 10**18:064x}'
    result = wallets.capture_wallets(prime, {day: {'ethereum': pin}}, call=call)
    assert result['states'][0]['raw_balance'] == str(90_000_000 * 10**18)
    assert any(method == 'eth_call' for method, _ in captured)


@pytest.mark.parametrize('failure', ['empty', 'wrong_chain', 'bad_boundary', 'unfinalized', 'rpc_failure'])
def test_archive_capture_does_not_turn_missing_state_into_zero(failure):
    prime, _, observations, _, _, _ = dataset()
    day, pin = observations[6]['day'], observations[6]['pins']['ethereum']
    def call(chain, method, params):
        if failure == 'rpc_failure':
            raise RuntimeError('provider failure')
        if method == 'eth_chainId':
            return '0xa' if failure == 'wrong_chain' else '0x1'
        if method == 'eth_blockNumber':
            return hex(pin if failure == 'unfinalized' else 100000)
        if method == 'eth_getBlockByNumber':
            result = deepcopy(headers(day, pin)[int(params[0], 16) - pin])
            if failure == 'bad_boundary':
                result['timestamp'] = '0x1'
            return result
        return '0x'
    with pytest.raises((assets.AssetControlError, RuntimeError)):
        wallets.capture_wallets(prime, {day: {'ethereum': pin}}, call=call)


def test_real_july_base_archive_witness_rejects_a_copied_july5_balance():
    from dataclasses import replace
    from pathlib import Path

    from settle.domain.config import load_prime_by_id
    from settle.normalize.usds_bridge_transit import usds_amount

    source = json.loads((Path(__file__).parents[1] /
                         'fixtures/settlement_asset_controls/spark_july_wallets.json').read_text())
    prime = load_prime_by_id('spark')
    prime = replace(prime, alm={c: h for c, h in prime.alm.items()
                               if c in (Chain.ETHEREUM, Chain.BASE)}, alm_start_dates={})
    accounts = {c.value: {'chain': c.value, 'holder': h.hex, 'custody_id': USDS_BY_CHAIN[c].address.hex,
                           'deduction_fields': ['alm_usds']} for c, h in prime.alm.items()}
    observations = []
    for day in sorted({r['day'] for r in source['states']}):
        states = {r['chain']: {'block': r['block'], 'value_usd': usds_amount(int(r['raw_balance'])),
                               'deductions': {'alm_usds': usds_amount(int(r['raw_balance']))},
                               'evidence': ['wallets.json']} for r in source['states'] if r['day'] == day}
        observations.append({'day': day, 'assets': states})
    control = {'inventory': {'accounts': accounts}}
    wallets.check_wallet_states(prime, observations, control, {'wallets.json': source})
    assert D(observations[0]['assets']['base']['value_usd']) == D('146550618.210418117475041461')
    assert D(observations[1]['assets']['base']['value_usd']) == 0
    observations[1]['assets']['base']['value_usd'] = observations[0]['assets']['base']['value_usd']
    observations[1]['assets']['base']['deductions']['alm_usds'] = observations[0]['assets']['base']['value_usd']
    with pytest.raises(assets.AssetControlError, match='Stale wallet value/deduction on 2026-07-06/base'):
        wallets.check_wallet_states(prime, observations, control, {'wallets.json': source})


def test_native_bridge_control_reuses_gateway_history_and_excludes_delivery_day():
    from settle.domain.config import load_prime_by_id
    from settle.normalize.usds_bridge_transit import collect_transfers, daily_exposure
    from tests.unit.test_usds_bridge_transit import period, reader, sample

    prime = load_prime_by_id('spark')
    p = period()
    source = bridges.capture_bridges(prime, p, fetch=reader(sample()))
    transfers = collect_transfers(prime, p, fetch=reader(sample()))
    opening = Period(p.start - timedelta(days=1), p.end, p.pin_blocks)
    observations = [{'day': str(row['block_date']), 'assets': {
        'bridge': {'value_usd': str(row['cum_balance']),
                   'deductions': {'bridge_usds': str(row['cum_balance'])}}}}
        for row in daily_exposure(opening, transfers).to_dict('records')]
    control = {'inventory': {'accounts': {'bridge': {'chain': 'arbitrum', 'deduction_fields': ['bridge_usds']}}}}
    bridges.check_bridge_states(prime, p, observations, control, source)
    received = next(r for r in observations if r['day'] == '2026-10-19')
    received['assets']['bridge']['value_usd'] = '1'
    received['assets']['bridge']['deductions']['bridge_usds'] = '1'
    with pytest.raises(assets.AssetControlError, match='Gateway/owned bridge claims disagree on 2026-10-19'):
        bridges.check_bridge_states(prime, p, observations, control, source)
    source['queries'].pop()
    with pytest.raises(assets.AssetControlError, match='Missing complete gateway query'):
        bridges.check_bridge_states(prime, p, observations, control, source)


@pytest.mark.parametrize('change,match', [
    (lambda c, r: r.update(status='pending'), 'complete reviewed'),
    (lambda c, r: r.update(unclassified_count=1), 'complete reviewed'),
    (lambda c, r: r.update(config_sha256='stale'), 'stale configuration'),
    (lambda c, r: c.update(month='2026-08'), 'Wrong prime/month'),
    (lambda c, r: r.update(asset_inventory_sha256='stale'), 'has not been reviewed'),
    (lambda c, r: r.update(scan_coverage={}), 'wallet coverage'),
    (lambda c, r: next(iter(r['scan_coverage'].values())).update(to_block=4000), 'does not cover'),
    (lambda c, r: next(iter(r['scan_coverage'].values())).update(status='partial'), 'Incomplete reviewed'),
    (lambda c, r: next(iter(r['scan_coverage'].values())).update(token_standards=['erc20']), 'token scan scope'),
])
def test_partial_reviews_cannot_issue_a_passing_result(tmp_path, monkeypatch, change, match):
    path, pnl = write_packet(tmp_path, monkeypatch)
    control = json.loads(path.read_text())
    review_path = path.parent / 'review.json'
    review = json.loads(review_path.read_text())
    change(control, review)
    path.write_text(json.dumps(control))
    review_path.write_text(json.dumps(review))
    with pytest.raises(assets.AssetControlError, match=match):
        assets.validate_packet(path, pnl, repo_root=tmp_path)


def test_balanced_inventory_and_matching_saved_deductions_still_need_real_wallet_proof(tmp_path, monkeypatch):
    path, pnl = write_packet(tmp_path, monkeypatch)
    obs_path = path.parent / 'observations.json'
    observations = json.loads(obs_path.read_text())
    observations[13]['assets']['wallet'].update(value_usd=str(TOTAL), deductions={'alm_usds': str(TOTAL)})
    observations[13]['assets']['destination']['value_usd'] = '0'
    pnl.sky_revenue_daily[12]['alm_usds'] = str(TOTAL)
    obs_path.write_text(json.dumps(observations))
    for p in (path, path.parent / 'review.json'):
        record = json.loads(p.read_text())
        record['evidence_sha256']['observations.json'] = assets.sha256(obs_path)
        p.write_text(json.dumps(record))
    with pytest.raises(assets.AssetControlError, match='Stale wallet value/deduction on 2026-07-13'):
        assets.validate_packet(path, pnl, repo_root=tmp_path)


def test_evidence_must_be_inside_review_directory_and_duplicate_json_keys_fail(tmp_path):
    outside = tmp_path / 'secret.json'
    outside.write_text('{}')
    root = tmp_path / 'review'
    root.mkdir()
    (root / 'alias.json').symlink_to(outside)
    with pytest.raises(assets.AssetControlError, match='inside the review directory'):
        assets._evidence(root, 'alias.json')
    duplicate = root / 'duplicate.json'
    duplicate.write_text('{"amount":"1","amount":"2"}')
    with pytest.raises(assets.AssetControlError, match='Duplicate JSON key'):
        assets._json(duplicate)


def test_idle_eligibility_respects_configured_alm_activation():
    from dataclasses import replace

    prime, control, observations, _, _, raw = dataset()
    prime = replace(prime, alm_start_dates={ETH: date(2026, 7, 6)})
    for observation in observations[:6]:
        observation['assets']['wallet']['deductions']['alm_usds'] = '0'
    wallets.check_wallet_states(prime, observations, control, {'wallets.json': raw, 'raw.json': {}})
    observations[5]['assets']['wallet']['deductions']['alm_usds'] = str(TOTAL)
    with pytest.raises(assets.AssetControlError, match='Stale wallet value/deduction on 2026-07-05'):
        wallets.check_wallet_states(prime, observations, control, {'wallets.json': raw, 'raw.json': {}})


def test_future_alm_is_not_requested_from_an_inactive_chain():
    from dataclasses import replace

    prime, _, observations, _, _, _ = dataset()
    prime = replace(prime, alm_start_dates={ETH: date(2026, 8, 1)})
    def forbidden(*args):
        pytest.fail('Inactive chain reached RPC')
    assert wallets.capture_wallets(prime, {observations[1]['day']: {}}, call=forbidden)['states'] == []
