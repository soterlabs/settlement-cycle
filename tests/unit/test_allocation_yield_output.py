from decimal import Decimal

from openpyxl import Workbook

from scripts.build_settlement_xlsx import _write_allocation_yields
from settle.load.summary import render_summary


def test_reports_preserve_zero_and_missing_net_yield():
    analytics = {'allocations': [
        {'venue_id': 'V1', 'borrowed_principal_average': '100', 'cost_of_funds': '1',
         'net_pnl': '0', 'gross_apy': Decimal('0.0488'), 'net_apy': Decimal(0)},
        {'venue_id': 'V2', 'borrowed_principal_average': None, 'cost_of_funds': None,
         'net_pnl': None, 'gross_apy': Decimal('0.03'), 'net_apy': None}]}
    wb = Workbook()
    _write_allocation_yields(wb.active, analytics)
    assert wb.active['E2'].value == 0.0488
    assert wb.active['F2'].value == 0
    assert wb.active['F3'].value is None
    prov = {'prime_id': 'obex', 'month': '2026-08',
        'period': {'start': '2026-08-01', 'end': '2026-08-31', 'n_days': 31},
        'results': {'prime_agent_revenue': '0', 'agent_rate': '0',
                    'sky_revenue': '0', 'monthly_pnl': '0'},
        'venue_breakdown': [], 'allocation_financing': analytics}
    md = render_summary(prov)
    assert '| V1 | $100.00 | $1.00 | $0.00 | 4.8800% | 0.0000% |' in md
    assert '| V2 | — | — | — | 3.0000% | — |' in md
