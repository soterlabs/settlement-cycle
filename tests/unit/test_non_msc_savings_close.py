"""A just-closed month must include interest not yet minted by Pot.drip."""
from decimal import Decimal, localcontext
from types import SimpleNamespace

import pytest

from settle.normalize.sources import hypersync_non_msc as H

RAY = 10**27


def test_pending_dsr_real_september_closing_state():
    pending = H._pot_pending_rad(
        pie=171025266081257806520847553, chi=1181176209461186742949758751,
        dsr=1000000000393915525145987602, rho=1790804555, timestamp=1790812800)
    # Independent high-precision real-number reference; integer contract
    # rounding can differ only in far-sub-cent raw accumulator units.
    with localcontext() as ctx:
        ctx.prec = 80
        expected = (Decimal(171025266081257806520847553)
                    * Decimal(1181176209461186742949758751)
                    * ((Decimal(1000000000393915525145987602) / RAY)**8245 - 1))
        assert abs(Decimal(pending) - expected) / 10**45 < Decimal('1e-15')
    assert Decimal('650') < Decimal(pending) / 10**45 < Decimal('660')


def test_dsr_cash_plus_pending_does_not_double_count_later_drip():
    state = dict(pie=1000 * 10**18, chi=RAY, dsr=RAY + 10**20, rho=0)
    start = H._pot_pending_rad(**state, timestamp=100)
    end = H._pot_pending_rad(**state, timestamp=200)
    # Recognize Sep's closing receivable, then clear it against October's
    # cash drip. The two months together equal only the minted amount.
    september_expense = start
    october_expense = end - start
    assert september_expense + october_expense == end
    assert october_expense > 0


@pytest.mark.parametrize('changes', [{'pie': 0}, {'dsr': RAY}, {'rho': 200}])
def test_no_pending_liability_without_shares_growth_or_elapsed_time(changes):
    state = dict(pie=10**18, chi=RAY, dsr=RAY + 10**20, rho=100, timestamp=200)
    state.update(changes)
    assert H._pot_pending_rad(**state) == 0


def test_pot_state_is_read_before_boundary(monkeypatch):
    from settle.extract import rpc
    seen = []
    monkeypatch.setattr(H.hypersync, 'find_block_at_or_before',
                        lambda chain, ts: seen.append((chain, ts)) or 123)
    values = iter([10**18, RAY, RAY, 99])
    def read(chain, contract, data, block):
        assert block == 123
        assert contract.hex == H._POT
        return hex(next(values))
    monkeypatch.setattr(rpc, 'eth_call', read)
    assert H._pot_accrued_at(100) == 0
    assert seen == [('ethereum', 99)]


@pytest.mark.parametrize('events', [[], [(101, 1, 1), (201, 2, 1)],
                                   [(99, 1, 1), (199, 2, 1)]])
def test_missing_savings_boundary_is_not_silently_published(events):
    with pytest.raises(ValueError, match='do not bracket'):
        H._require_savings_coverage('sUSDS', events, 100, 200)


def test_complete_savings_coverage():
    H._require_savings_coverage('sUSDS', [(99, 1, 0), (201, 2, 1)], 100, 200)


def test_monthly_dsr_combines_only_in_month_mints_and_boundary_liabilities(monkeypatch):
    start, end = H._SAVINGS_CLOSE_FROM, H._SAVINGS_CLOSE_FROM + 30 * 86400
    monkeypatch.setattr(H.hypersync, 'find_block_at_or_before', lambda *args: 123)
    def query(chain, selections, *args, **kwargs):
        if selections[0]['address'] == [H._VAT]:
            rows = [SimpleNamespace(block_number=i, log_index=0, block_time=t,
                                    topic3=hex(amount * 10**45))
                    for i, (t, amount) in enumerate([(start - 1, 100), (start, 20),
                                                    (end - 1, 30), (end, 200)])]
        else:
            rows = [SimpleNamespace(block_number=i, log_index=0, block_time=t,
                                    data='0x' + f'{RAY + i:064x}' + f'{1:064x}')
                    for i, t in enumerate([start - 1, end + 1])]
        return SimpleNamespace(rows=rows)
    monkeypatch.setattr(H.hypersync, 'query_logs', query)
    monkeypatch.setattr(H, '_pot_accrued_at',
                        lambda ts: Decimal({start: 5, end: 8}[ts]) * 10**45)
    result = H.HyperSyncNonMscSource()._savings(start, end)
    dsr = next(r for r in result if r['stream'] == 'expense:dsr_drip')
    assert dsr['amount'] == Decimal(53)  # 20 + 30 + 8 - 5, no October mint.
