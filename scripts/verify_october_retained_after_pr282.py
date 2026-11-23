"""Read-only October arithmetic replay; never substitutes for asset controls.

Replays the production daily borrowing and prime mint/send formulas from the
hash-bound retained reports. This is not a fresh positions extraction, a full
portfolio attestation, or a canonical report writer. No network is permitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import subprocess
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import openpyxl
import pandas as pd
import yaml

from settle.compute import sky_total_accrual
from settle.compute.sky_revenue import compute_sky_revenue_daily
from settle.domain import Chain, Month, Period
from settle.domain.config import load_prime_by_id
from settle.domain.sky_tokens import USDS_BY_CHAIN
from settle.extract.hypersync import LogRow
from settle.normalize.usds_bridge_transit import collect_transfers, daily_exposure
from settle.revenue.monthly import _reference_history

ROOT = Path(__file__).resolve().parents[1]
OLD_HEAD = '6820525e1ae34ca8460ac3a0dfc5f4cc68c80ae9'
SEAL_SHA256 = 'af8d972a4d772b80b422ba2f31c776d601db0015788d5d083666b7aa70d60f80'
MONTH = Month(2026, 10)


def check(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_source_seal():
    path = ROOT / 'reconciliation/2026-10/retained-source-seal.json'
    check(digest(path) == SEAL_SHA256, 'Original source seal changed')
    seal = json.loads(path.read_text())
    check(seal['schema'] == 1 and seal['source_commit'] == OLD_HEAD, 'Wrong source seal identity')
    return seal


def changed_production_paths(seal):
    # HEAD's tree exists even in a depth-one checkout. Historical commits may
    # have been rebased away; their immutable blob IDs are retained explicitly.
    raw = subprocess.check_output(['git', 'ls-tree', '-r', '-z', 'HEAD', '--',
                                   'src/settle', 'config'], cwd=ROOT)
    current = {}
    for entry in raw.split(b'\0'):
        if entry:
            metadata, path = entry.split(b'\t', 1)
            current[path.decode()] = metadata.decode().split()[2]
    original = seal['production_blob_ids']
    return sorted(path for path in original.keys() | current.keys()
                  if original.get(path) != current.get(path))


def optional_git_ref(ref):
    try:
        return subprocess.check_output(['git', 'rev-parse', '--verify', ref], cwd=ROOT,
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except subprocess.CalledProcessError:
        return None


def replay(wallet_inputs=None):
    seal = load_source_seal()
    changed = changed_production_paths(seal)
    allowed = {'src/settle/compute/allocation_cost_rows.py', 'src/settle/load/writer.py',
               'src/settle/normalize/sources/rpc_block_resolver.py',
               # PR283 adds presentation-only accrued yields; monthly compute
               # does not import this module or substitute accrued issuer income.
               'src/settle/revenue/allocation_incentives.py', 'config/sky_total.yaml',
               # PR284 exposes reporting API values; no MSC formula imports it.
               'src/settle/api/allocations.py'}
    # Treasury-only changes do not enter prime revenue/borrowing formulas.
    # Accept only the exact sources used in the independently replayed TMF close.
    treasury_sources = {'config/tmf.yaml': '342dac6b6ae23e560f137d3e51b5e6f72fcba4232a3b83275c4436910358b173', 'src/settle/compute/tmf.py': 'dbc7b661875b60474b8369e1e8fdde361940469b95ef6fd57fbc9fe990fd5cc8'}
    for path, sha in treasury_sources.items():
        if path in changed:
            check(digest(ROOT / path) == sha, 'Unreviewed treasury source change: ' + path)
            allowed.add(path)
    # PR285 only projects reporting statements and serves an expense snapshot.
    # None of these inputs enters October settlement computation. Bind the exact
    # reviewed files instead of broadly exempting future revenue-module changes.
    reporting_sources = {
        'src/settle/api/financial.py': '5a510e1df807a79fd2cc5379a1dfb4dca0b4badaac66c6583deb0c614b1208bb',
        'src/settle/api/allocations.py': 'd9b801eb16ab055fb53153c1fe7e53150bef7234614d91157ef0575c61c56188',
        'src/settle/revenue/margins.py': 'bd7b75f103839a5ba5d2e478a9562960112e0b98b744548e606a6d51f6879420',
        'src/settle/revenue/reporting_expenses.py': '8c31e4e67e4ab5a97edf40740a373e93bd1bd5ea24283d151ade13270807b56f',
        'src/settle/revenue/spark_expenses_2026.v1.json': 'cd770dc9a6b3065ea60bbdc10fa13b374c80c7038bba2b899d24f7a3610160a6',
        'src/settle/revenue/margin_balances.py': '2f751a909f1ef820e498516d4d335cf28c4726399f1b1f24e95767a5ada1771c',
        'src/settle/revenue/margin_balance_job.py': '3d860f42419379752786c23e75fac88558bc3f570fd7186b91b7cff9cbfd6c2f',
    }
    # PR288 adds closing-only entity inventory/consolidation; no MSC inputs change.
    reporting_sources.update({
        'src/settle/revenue/spark_anchorage_principal.v1.json': 'e582bba1d7e43848b417c40d0b85737b77b67ae9b08d760c89e64965d21ff6ef',
        'src/settle/revenue/anchorage_principal.py': 'b460138b9bc24f33bf54dea6c9418e0b1f530811d2afd21c22fa36d18a62fb37',
        'src/settle/revenue/balance_sheet.py': '67fac77f745ddd98480c92858a0ce2afbbdcc97c4e8a2470783a99fefb8de306',
        'src/settle/revenue/balance_sheet_sources.py': '38c13caa9e4c9adc5356883608cf94e170af9feacf3b1fae36e9e35f173a1587',
        'src/settle/revenue/balance_sheet_controls.py': 'c65b11e7563d0eb769e345d8a144004d813d2552c7b57340d696074429591c7f',
        'src/settle/revenue/balance_sheet_job.py': 'e25396e9ca2da46811aa04920da1b99fea91d9a6addd2ea44457d5d4f114fda6',
        'src/settle/revenue/balance_sheet_store.py': '10cfb0303ec03794048462786e45e7576dd21b59f3d10183f11cb0d3971643bc',
        'src/settle/revenue/BALANCE_SHEET_GUIDE.md': 'faeb167ff5005593d4a30c79f641b7803a9d393827a6ff21b8f3d699863e31e1',
    })
    # Human-readable Anchorage receipt attribution only; retained MSC inputs unchanged.
    reporting_sources['src/settle/load/loan_income_display.py'] = '7f43c1dc88a75541ef71a6ac18ac8a9cdaef266f0401c673c3ec1bce8f06c24c'
    reporting_sources['src/settle/load/summary.py'] = '34b0381261040e2d2ca203927a19e899289a2f6fcdfdea07b5e15d5c8c8e33a1'
    # PR291 stops synthesizing Grove's tracing-only E12_PAU capital account when
    # configured venue E44 already holds that Diamond PAU NFT. The module is only
    # reached through compute_monthly_pnl(include_allocation_financing=True),
    # which no settlement, daily worker or API path enables; MSC revenue and
    # borrowing formulas do not import it. Bind the exact reviewed file.
    reporting_sources['src/settle/normalize/allocation_capital.py'] = '4d027543ab38915bb38b27037cc8e0feda3938fd73435081ef5ee33935f37073'
    # Operational driver that re-runs the unchanged daily worker one cutoff at a
    # time for stale observations; no settlement computation imports it.
    reporting_sources['src/settle/revenue/replay.py'] = '0f113a224f1dbc94fdb022c8101d2cc87f5b2c2e29f6cca58b41dcad3dd0e2fa'
    for path, sha in reporting_sources.items():
        if path in changed:
            check(digest(ROOT / path) == sha, 'Unreviewed reporting source change: ' + path)
            allowed.add(path)
    if any(p not in allowed and not p.startswith('src/settle/controls/') for p in changed):
        raise ValueError('Financial/configuration changes require a scoped source replay')
    folder = ROOT / 'reconciliation/2026-10'
    preview_path = folder / 'retained-close/prime_msc_preview.json'
    check(seal['preview_sha256'] == digest(preview_path), 'Original preview changed; use a scoped financial replay')
    preview = json.loads(preview_path.read_text())
    retained = folder / 'retained-close'
    cfg_path = retained / 'sky_total.yaml' if retained.exists() else ROOT / 'config/sky_total.yaml'
    check(digest(cfg_path) == preview['sky_total_config_sha256'], 'MSC configuration changed')
    cfg = yaml.safe_load(cfg_path.read_text())
    live_cfg = yaml.safe_load((ROOT / 'config/sky_total.yaml').read_text())
    if live_cfg != cfg:
        import copy
        expected = copy.deepcopy(cfg)
        from calculate_october_partner_rebate import replacements
        historical = json.loads((ROOT / 'reconciliation/2026-01_to_2026-09/data.json').read_text())
        expected['msc_preview']['2026-10'].update(replacements(historical))
        check(live_cfg == expected, 'Unexpected configuration change outside historical replacements')
    reference_path = ROOT / 'config/october_2026_official_reference_rates.json'
    reference = json.loads(reference_path.read_text())
    grove = json.loads((folder / 'grove-pr280/after-provenance.json').read_text())
    basin = {r['date']: r['basin_ilk_debt'] for r in grove['sky_revenue_daily']}
    wallets = None
    if wallet_inputs is not None:
        check(digest(wallet_inputs) == 'd321abe8c8da9f4eb255ed27ffc0116e8c91168fc693b9c0d59e4acb0ab74469',
              'Wallet evidence differs from reviewed PR281 snapshot f71f8d6 (June 20-21 added; October unchanged)')
        wallets = json.loads(wallet_inputs.read_text())
    results, sources = {}, {str(p.relative_to(ROOT)): digest(p) for p in (
        preview_path, cfg_path, reference_path, folder / 'grove-pr280/after-provenance.json',
        folder / 'retained-source-seal.json')}
    for entry in preview['rows']:
        name = entry['prime']
        prime = load_prime_by_id(name, config_dir=ROOT / 'config')
        report = retained / name if retained.exists() else ROOT / 'settlements' / name / str(MONTH)
        workbook = report / f'{name}_settlement_october_2026.xlsx'
        check(digest(workbook) == entry['workbook_sha256'], f'{name}: workbook changed')
        check(digest(report / 'summary.md') == entry['summary_sha256'], f'{name}: summary changed')
        sources[str(workbook.relative_to(ROOT))] = digest(workbook)
        book = openpyxl.load_workbook(workbook, read_only=True, data_only=True)
        try:
            rows = list(book['Debt'].values)
        finally:
            book.close()
        header = next(r for r in rows if r[0] == 'Date')
        rows = [r for r in rows if isinstance(r[0], str) and r[0].startswith('2026-10-')]
        dates = [MONTH.first_day + timedelta(days=i) for i in range(31)]
        check([r[0] for r in rows] == [str(d) for d in dates], f'{name}: incomplete dates')
        def values(column, header=header, rows=rows):
            # The renderer omits bridge/Basin columns only when entirely zero.
            if column not in header:
                check(column in {'- In-transit USDS', '- Basin idle'}, f'Missing workbook field: {column}')
                return [D(0)] * 31
            return [D(str(r[header.index(column)])) for r in rows]
        def series(column, target='cum_balance', dates=dates, values=values):
            return pd.DataFrame({'block_date': dates, target: values(column)})
        check((prime.basin_idle_usds is not None) == (name == 'grove'), 'Basin scope changed')
        basin_rows = pd.DataFrame({'block_date': dates, 'cum_balance': values('- Basin idle'),
                                  'ilk_debt': [D(basin[str(d)]) if name == 'grove' else D(0) for d in dates]})
        period = Period.from_month(MONTH)
        _, daily, _ = compute_sky_revenue_daily(
            period, series('cum_debt', 'cum_debt'), series('\u2212 ALM idle'),
            pd.DataFrame({'effective_date': dates, 'ssr_apy': values('SSR APY')}),
            psm_usds=series('\u2212 PSM USDS', 'cum_usds_leg'),
            sde_asset_value=series('\u2212 SDE NAV', 'cum_value'),
            curve_idle_usds=series('\u2212 Curve idle'), lending_idle_usds=series('\u2212 Lending idle'),
            basin_idle_usds=basin_rows, bridge_usds=series('- In-transit USDS'),
            subsidy_config=prime.subsidy,
            ref_rate_history=_reference_history(prime, period, {'reference_rates': reference}),
        )
        checks = {'daily_sky_rev': values('daily Sky charge'),
                  'daily_sky_rev_gross': values('daily Sky charge (gross on cum_debt)'),
                  'utilized': [D(str(r[header.index('SSR APY')-1])) for r in rows]}
        deviations = {key: max(abs(D(str(actual))-expected) for actual, expected in
                               zip(daily[key], wanted, strict=True)) for key, wanted in checks.items()}
        check(all(delta <= D('.01') for delta in deviations.values()), f'{name}: borrowing deviations {deviations}')
        native_check = {}
        if name == 'spark' and wallets is not None:
            expected = {c.value: (holder.hex, USDS_BY_CHAIN[c]) for c, holder in prime.alm.items() if c in USDS_BY_CHAIN}
            check(set(wallets['chains']) == set(expected), 'Incomplete wallet chain evidence')
            for chain, (holder, token) in expected.items():
                descriptor = wallets['chains'][chain]
                check((descriptor['holder'], descriptor['token'], descriptor['decimals']) ==
                      (holder, token.address.hex, token.decimals), 'Wallet ownership/token mismatch')
            raw_values = [sum((D(wallets['days'][str(day)][c]['raw_balance']) / D(10**18)
                               for c in expected), D(0)) for day in dates]
            error = max(abs(a-b) for a,b in zip(raw_values, values('\u2212 ALM idle'), strict=True))
            check(error <= D('.01'), 'October ALM idle differs from actual archive holdings')
            gateway_path = ROOT / 'tests/fixtures/usds_bridge/spark_2026_gateway_logs.json'
            logs = json.loads(gateway_path.read_text())
            def fetch(chain, selections, start, end, logs=logs, **kwargs):
                return [LogRow(**row) for row in logs[chain] if start <= row['block_number'] <= end
                        and row['address'] in selections[0]['address'] and row['topic0'] in selections[0]['topics'][0]]
            pinned = Period.from_month(MONTH, {Chain(c): wallets['days']['2026-10-31'][c]['block'] for c in expected})
            claims = daily_exposure(pinned, collect_transfers(prime, pinned, fetch=fetch))
            check(all(abs(a-b) <= D('.01') for a,b in zip(claims['cum_balance'], values('- In-transit USDS'), strict=True)),
                  'October bridge deduction differs from authenticated gateway events')
            sources[str(gateway_path.relative_to(ROOT))] = digest(gateway_path)
            native_check = {'wallet_days_compared': 31, 'wallet_observations': 31 * len(expected),
                            'max_wallet_difference': str(error), 'native_bridge_days_matched': 31,
                            'wallet_source_sha256': digest(wallet_inputs), 'wallet_source_commit': 'f526b0bdc1d60d37a5f30a807c51a19f2f183ac0',
                            'wallet_source_path': 'reconciliation/usds-bridge-2026/baseline-correction/wallet-inputs.json',
                            'scope': 'Partial independent native-USDS check only; not a whole-portfolio attestation'}
        results[name] = {'days_replayed': 31, 'max_workbook_precision_difference':
                         {key: str(value) for key, value in deviations.items()},
                         'independent_native_usds_check': native_check,
                         'asset_control_packet_present': (ROOT / '.msc-transfer-reviews' / name /
                                                          str(MONTH) / 'asset-controls.json').exists()}
    indexed = {row['prime']: row for row in preview['rows']}
    def components(root, prime, month):
        row = indexed[prime]
        return tuple(D(row[k]) for k in ('sky_ordinary_total', 'demand_side_revenue', 'ordinary_supply_net'))
    # Inject actual retained aggregate components into the existing reader.
    # Non-MSC is outside this research scope: no consolidated result is exported.
    with TemporaryDirectory(prefix='pr275-retained-projection-') as directory, \
         patch.object(sky_total_accrual, '_load_prime_components', components), \
         patch.object(sky_total_accrual, '_load_non_msc',
                      lambda *a: (D(0), D(0), ['Non-MSC not assessed; consolidated total not exported'])):
        projection = Path(directory)
        for name in indexed:
            metadata = projection / 'settlements' / name / str(MONTH) / 'provenance.json'
            metadata.parent.mkdir(parents=True)
            metadata.write_text(json.dumps({'scope': 'Research metadata projection only; no financial report or payment history asserted'}))
        totals = sky_total_accrual.compute_sky_total_accrual(MONTH, repo_root=projection, config=cfg)
    for row in totals.rows:
        old = indexed[row.prime]
        check(row.derived_mint == D(old['msc_mint_unrounded']), f'{row.prime}: unrounded mint changed')
        check(row.derived_send == D(old['msc_send_unrounded']), f'{row.prime}: unrounded send changed')
        check(row.mint == D(old['msc_mint']) and row.send == D(old['msc_send']), f'{row.prime}: instructions changed')
        results[row.prime].update(mint=str(row.mint), send=str(row.send), instruction_change='0')
    return {'schema': 1, 'replay_script_sha256': digest(Path(__file__)),
            'production_function_sha256': {p: digest(ROOT / p) for p in
                ['src/settle/compute/sky_revenue.py', 'src/settle/compute/sky_total_accrual.py',
                 'src/settle/revenue/monthly.py']},
            'scope': 'retained October arithmetic only; not full asset controls',
            'main_commit': optional_git_ref('origin/main'), 'replay_code_commit': optional_git_ref('HEAD'),
            'prior_pr_head': OLD_HEAD, 'changed_production_paths': changed,
            'input_sha256': sources, 'primes': results, 'canonical_reports_written': False,
            'fresh_canonical_finalization': 'blocked until complete reviewed asset controls exist',
            'network_access': 'blocked'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--spark-wallet-inputs', type=Path, help='Optional closed PR281 archive evidence snapshot')
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT / 'settlements'):
        parser.error('Use a separate research output path')
    def denied(*args, **kwargs):
        raise AssertionError('Retained arithmetic replay attempted network access')
    with patch.object(socket.socket, 'connect', denied):
        result = replay(args.spark_wallet_inputs)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result['primes'], indent=2))


if __name__ == '__main__':
    main()
