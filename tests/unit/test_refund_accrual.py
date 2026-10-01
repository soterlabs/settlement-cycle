import json
from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pandas as pd
import pytest
import yaml

from settle.compute.non_msc import compute_non_msc_monthly, render_summary
from settle.domain import Month
from settle.extract.hypersync import LogRow
from settle.normalize.sources import refund_accrual as refunds


def fixture():
    data = json.loads((Path(__file__).parents[1] / 'fixtures/gelato_refund_september_2026.json').read_text())
    keys = {f.name for f in fields(LogRow)}
    receipt, settlement = [LogRow(**{k: v for k, v in data[name].items() if k in keys})
                           for name in ['receipt', 'settlement']]
    entry = yaml.safe_load(refunds.CONFIG.read_text())['accrued_refunds'][0]
    return entry, receipt, settlement


def fetcher(receipt, settlement):
    def fetch(chain, selection, lo, hi, **kwargs):
        rows = [receipt] if selection[0]['address'] == [receipt.address] else [settlement]
        return [r for r in rows if r is not None and lo <= r.block_number <= hi]
    return fetch


def test_actual_september_receipt_and_blow_are_recognized_once():
    entry, receipt, settlement = fixture()
    result = refunds.refund_adjustments(Month(2026, 9), 26093737,
                                       entries=[entry], fetch=fetcher(receipt, settlement))
    assert [r['amount'] for r in result] == [D('42469.146527'), -D('42469.146527')]
    assert [r['date'] for r in result] == ['2026-09-07', '2026-09-15']
    assert sum(r['amount'] for r in result) == 0  # cash stream already has the refund


def test_pending_refund_accrues_before_blow():
    entry, receipt, _ = fixture()
    result = refunds.refund_adjustments(Month(2026, 9), receipt.block_number,
                                       entries=[entry], fetch=fetcher(receipt, None))
    assert len(result) == 1 and result[0]['amount'] == D('42469.146527')


def test_cross_month_settlement_offsets_only_previous_accrual():
    entry, receipt, settlement = fixture()
    settlement = replace(settlement, block_number=26100000,
                         block_time=int(datetime(2026, 10, 2, tzinfo=UTC).timestamp()))
    september = refunds.refund_adjustments(Month(2026, 9), 26093737,
                                           entries=[entry], fetch=fetcher(receipt, settlement))
    october = refunds.refund_adjustments(Month(2026, 10), 26200000,
                                         entries=[entry], fetch=fetcher(receipt, settlement))
    assert september[0]['amount'] == D('42469.146527')
    assert len(october) == 1 and october[0]['amount'] == -D('42469.146527')


@pytest.mark.parametrize('problem', ['missing', 'amount', 'short_blow', 'duplicate'])
def test_unverified_or_duplicate_refund_fails(problem):
    entry, receipt, settlement = fixture()
    entries = [entry]
    if problem == 'missing':
        receipt = replace(receipt, transaction_hash='0xwrong')
    elif problem == 'amount':
        receipt = replace(receipt, data='0x' + f'{1000:064x}')
    elif problem == 'short_blow':
        settlement = replace(settlement, data='0x' + f'{1000:064x}')
    else:
        entries *= 2
    with pytest.raises(ValueError):
        refunds.refund_adjustments(Month(2026, 9), 26093737,
                                  entries=entries, fetch=fetcher(receipt, settlement))


def test_report_preserves_total_and_names_gelato(monkeypatch):
    entry, receipt, settlement = fixture()
    adjustment = refunds.refund_adjustments(Month(2026, 9), 26093737,
                                           entries=[entry], fetch=fetcher(receipt, settlement))
    monkeypatch.setattr(refunds, 'refund_adjustments', lambda *a: adjustment)
    def rows(month, pin):
        return pd.DataFrame([
            {'stream': 'income:surplus_return', 'label': '2026-09-15', 'amount': D('42469.146527')},
            {'stream': 'income:psm_jar', 'label': '2026-09-10', 'amount': D('100')},
        ])
    result = compute_non_msc_monthly(Month(2026, 9), 26093737, source=rows)
    assert result.total_income == D('42569.146527')
    assert result.refund_accrual_adjustment == 0
    text = render_summary(result)
    assert 'Gelato keeper surplus refund' in text and receipt.transaction_hash in text
    assert settlement.transaction_hash in text
    with pytest.raises(ValueError, match='absent from cash'):
        compute_non_msc_monthly(Month(2026, 9), 26093737, source=lambda *a: rows(*a).iloc[1:])
