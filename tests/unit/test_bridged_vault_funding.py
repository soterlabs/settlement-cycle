import importlib
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_capital import AssetMovement as M
from settle.normalize.allocation_capital import CapitalBatch as B
from settle.normalize.allocation_capital import CapitalHistory as H

DAY = date(2026, 1, 1)


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    return importlib.import_module('audit_bridged_vault_funding')


def m(a, before, change, income='0'):
    return M(a, D(before), D(change), D(income))


def b(n, *ms, draw='0'):
    return B(str(n), DAY, n, 'base', n, tuple(ms), D(draw),
             minted_by_ilk={'ilk': D(draw)} if D(draw) else {})


def run(module, rows):
    return module.attribute(H(tuple(rows), {'V': 'vault', 'C': 'cash'}, {}, {}),
                            'V', 'C', DAY, DAY)


def first_deposit():
    return [
        b(1, m('cctpv2:ethereum:a', '0', '100'), draw='100'),
        b(2, m('cctpv2:ethereum:a', '100', '-100'), m('cash', '0', '100')),
        b(3, m('cash', '100', '-100'), m('vault', '0', '100')),
    ]


def test_reinvested_gain_is_never_relabeled_as_borrowed(module):
    rows = [*first_deposit(),
            b(4, m('vault', '110', '-110'), m('cash', '0', '110')),
            b(5, m('cctpv2:ethereum:b', '0', '90'), draw='90'),
            b(6, m('cctpv2:ethereum:b', '90', '-90'), m('cash', '110', '90')),
            b(7, m('cash', '200', '-200'), m('vault', '0', '200')),
            b(8, m('other-cash', '500', '-500'), draw='-500')]
    r = run(module, rows)
    assert {k: D(v) for k, v in r['closing_origin'].items()} == {
        'borrowed': 190, 'reinvested_gains': 10, 'unresolved': 0}
    assert D(r['closing_capital']) == 200


def test_existing_cash_is_not_silently_assigned_borrowed_origin(module):
    rows = first_deposit()
    rows[0] = b(1, m('eth-cash', '40', '-40'), m('cctpv2:ethereum:a', '0', '100'), draw='60')
    rows.append(b(4, m('vault', '120', '-60'), m('cash', '0', '60')))
    r = run(module, rows)
    assert {k: D(v) for k, v in r['closing_origin'].items()} == {
        'borrowed': 30, 'reinvested_gains': 0, 'unresolved': 20}
    assert {k: D(v) for k, v in r['withdrawal_cash_origin']['4'].items()} == {
        'borrowed': 30, 'reinvested_gains': 10, 'unresolved': 20}


def test_competing_destination_prevents_claim_of_exact_source_mix(module):
    batch = b(1, m('eth-cash', '50', '-50'), m('other-vault', '0', '50'),
              m('cctpv2:ethereum:a', '0', '100'), draw='100')
    r = module.bridge_origin(batch, 'cctpv2:ethereum:a', D(100))
    assert r['borrowed'] == 0
    assert r['unresolved'] == 100


def test_nonconserving_draw_is_not_accepted(module):
    batch = b(1, m('cctpv2:ethereum:a', '0', '100'), draw='99')
    assert module.bridge_origin(batch, 'cctpv2:ethereum:a', D(100))['borrowed'] == 0


def test_reused_bridge_principal_is_rejected(module):
    rows = [*first_deposit(),
            b(4, m('cctpv2:ethereum:a', '100', '-100'), m('cash', '0', '100')),
            b(5, m('cash', '100', '-100'), m('vault', '100', '100'))]
    with pytest.raises(ValueError, match='allocated twice'):
        run(module, rows)


def test_loss_does_not_create_negative_gains(module):
    rows = [*first_deposit(), b(4, m('vault', '90', '-90'), m('cash', '0', '90'))]
    r = run(module, rows)
    assert D(r['withdrawal_cash_origin']['4']['borrowed']) == 90
    assert D(r['withdrawal_cash_origin']['4']['reinvested_gains']) == 0
    assert D(r['closing_capital']) == 0


def test_reviewed_grove_routes_reconcile_closing_principal(module):
    from settle.normalize.allocation_history_cache import load_history

    path = Path(__file__).resolve().parents[1] / 'fixtures/grove_steakhouse_funding_origins.jsonl.gz'
    history = load_history(path, 'grove-steakhouse-reviewed-origins-v1')
    result = module.attribute(history, 'E23', 'E27', DAY, date(2026, 8, 31))
    assert D(result['closing_capital']).quantize(D('.01')) == D('202695318.75')
    origin = result['closing_origin']
    assert D(origin['borrowed']).quantize(D('.01')) == D('193695912.39')
    assert D(origin['reinvested_gains']).quantize(D('.01')) == D('1485.61')
    assert D(origin['unresolved']) == D('8997920.746262')
    # Every outstanding unresolved dollar has an explicitly retained source.
    unresolved = [p for p in result['source_proofs'].values() if D(p['origin']['unresolved'])]
    assert len(unresolved) == 6
    assert all(p['prior_source_windows'] for p in unresolved)
    extended = module.attribute(history, 'E23', 'E27', DAY, date(2026, 8, 31),
                                reallocation_venues=('E1',))
    origin = extended['closing_origin']
    assert D(origin['borrowed']).quantize(D('.01')) == D('193695912.39')
    assert D(origin['reinvested_gains']).quantize(D('.01')) == D('37629.35')
    assert D(origin['unresolved']).quantize(D('.01')) == D('8961777.01')
    links = [p['upstream_reallocation'] for p in extended['source_proofs'].values()
             if p['upstream_reallocation']]
    assert len(links) == 1
    assert links[0]['source_venue'] == 'E1'
    upstream = module.attribute(history, 'E23', 'E27', DAY, date(2026, 8, 31),
                                reallocation_venues=('E1',), origin_group=('E1', 'E3', 'E13'))
    origin = upstream['closing_origin']
    assert D(origin['borrowed']).quantize(D('.01')) == D('196610228.04')
    assert D(origin['reinvested_gains']).quantize(D('.01')) == D('37629.35')
    assert D(origin['unresolved']).quantize(D('.01')) == D('6047461.36')
    mixed = module.attribute(
        history, 'E23', 'E27', DAY, date(2026, 8, 31),
        reallocation_venues=('E1',), origin_group=('E1', 'E3', 'E13'),
        cash_origin_group=('E4', 'E9', 'E10', 'E14', 'E15', 'E21', 'E36'))
    origin = mixed['closing_origin']
    assert D(origin['borrowed']).quantize(D('.01')) == D('202316398.04')
    assert D(origin['reinvested_gains']).quantize(D('.01')) == D('201059.46')
    assert D(origin['unresolved']).quantize(D('.01')) == D('177861.25')
    assert any(p['mixed_cash_origin'] for p in mixed['source_proofs'].values())


def test_cash_window_does_not_attribute_spending_fifo(module):
    rows = [b(1, m('cash', '0', '30', income='30')),
            b(2, m('other-vault', '70', '-70'), m('cash', '30', '70')),
            b(3, m('cash', '100', '-40'), draw='-40'),
            b(4, m('cash', '60', '-20'), m('cctpv2:ethereum:a', '0', '20'))]
    h = H(tuple(rows), {'V': 'vault', 'C': 'cash'}, {}, {})
    window = module.prior_cash_window(h, rows[-1], 'cash')
    assert window['starts_from_empty_cash']
    assert [r['transaction'] for r in window['transactions']] == ['1', '2', '3']
    assert window['transactions'][0]['explicit_cash_income'] == '30'


def test_upstream_gain_survives_partial_cash_bridge(module):
    rows = [
        b(1, m('source-vault', '0', '100')),
        b(2, m('source-vault', '120', '-120'), m('eth-cash', '0', '110')),
        b(3, m('eth-cash', '110', '-55'), m('cctpv2:ethereum:a', '0', '55')),
        b(4, m('cctpv2:ethereum:a', '55', '-55'), m('cash', '0', '55')),
        b(5, m('cash', '55', '-55'), m('vault', '0', '55')),
    ]
    h = H(tuple(rows), {'V': 'vault', 'C': 'cash', 'S': 'source-vault'}, {}, {})
    r = module.attribute(h, 'V', 'C', DAY, DAY, reallocation_venues=('S',))
    assert {k: D(v) for k, v in r['closing_origin'].items()} == {
        'borrowed': 0, 'reinvested_gains': 5, 'unresolved': 50}
    proof = r['source_proofs']['cctpv2:ethereum:a']['upstream_reallocation']
    assert proof['source_venue'] == 'S'
    assert D(proof['net_gain_after_conversion']) == 10  # Not the 20 token-mark gain.
    assert module.attribute(h, 'V', 'C', DAY, DAY)['closing_origin']['unresolved'] == '55'


def test_mixed_cash_does_not_inherit_one_sources_gain_mix(module):
    rows = [
        b(1, m('source-vault', '0', '100')),
        b(2, m('eth-cash', '0', '10', income='10')),
        b(3, m('source-vault', '110', '-110'), m('eth-cash', '10', '110')),
        b(4, m('eth-cash', '120', '-60'), m('cctpv2:ethereum:a', '0', '60')),
    ]
    h = H(tuple(rows), {'S': 'source-vault'}, {}, {})
    assert module.reallocation_origin(h, rows[-1], 'cctpv2:ethereum:a', D(60),
                                      ('S',), {}, DAY, DAY) is None
