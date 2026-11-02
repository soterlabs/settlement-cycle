"""Explain a gap without treating every difference as missing principal."""
import io
import json
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.normalize.allocation_capital import AssetMovement as M
from settle.normalize.allocation_capital import CapitalBatch as B
from settle.normalize.allocation_capital import CapitalHistory


def example():
    day = date(2026, 8, 1)
    return CapitalHistory((
        B('draw', day, 1, 'ethereum', 1, (M('venue', D(0), D(100)),), D(100), minted_by_ilk={'spark': D(100)}),
        B('income', day, 2, 'ethereum', 2, (M('cash', D(0), D(10), external_income=D(10)),)),
        B('repay', day, 3, 'ethereum', 3, (M('cash', D(10), D(-10)),), D(-10), minted_by_ilk={'spark': D(-10)}),
        B('unknown', day, 4, 'ethereum', 4, (M('venue', D(100), D(5)),)),
    ), {'S1': 'venue'}, {})


def test_transaction_bridge_separates_repayment_and_unknown_receipt(monkeypatch):
    monkeypatch.syspath_prepend(str(Path('scripts').resolve()))
    from audit_spark_funding_gap_events import GapEvents
    from report_allocation_capital_readiness import capital_timeline

    history = example()
    day = date(2026, 8, 1)
    stream = io.StringIO()
    def capture(batch, basis):
        stream.write(json.dumps({'identity': batch.identity, 'principal': {'S1': str(basis['venue'])}})+'\n')
        basis['venue'] = D(-123)  # Observer gets a copy, not the model's state.
    control = capital_timeline(history, day, day, on_batch=capture, on_batch_accounts={'venue'})
    assert control == capital_timeline(history, day, day)
    stream.seek(0)
    audit = GapEvents(stream, {'S1': ['venue']}, day, limit=2)
    measured = replay_history(history, day, day, funding_accounts={'venue'}, funding_observer=audit.observe)
    assert measured == replay_history(history, day, day, funding_accounts={'venue'})
    result = audit.result()
    assert result['closing_gaps']['S1'] == 15
    assert result['inception_contributions']['S1'] == {'repayment_effect': D(10), 'other_effect': D(5)}
    events = result['largest_events']['S1']['period']
    assert [e['identity'] for e in events] == ['repay', 'unknown']
    assert events[0]['repayment_effect'] == 10
    assert events[1]['other_effect'] == 5
    assert result['publishable'] is False


def test_event_alignment_and_trailing_control_are_rejected(monkeypatch):
    monkeypatch.syspath_prepend(str(Path('scripts').resolve()))
    from audit_spark_funding_gap_events import GapEvents

    batch = example().batches[0]
    audit = GapEvents(iter([json.dumps({'identity': 'wrong', 'principal': {'S1': '100'}})]),
                      {'S1': ['venue']}, batch.day)
    with pytest.raises(ValueError, match='transaction order'):
        audit.observe(batch, 'batch_end', {'venue': D(100)})
    audit = GapEvents(iter(['unconsumed']), {'S1': ['venue']}, batch.day)
    with pytest.raises(ValueError, match='omitted control'):
        audit.result()


def test_savings_repayment_is_measured_separately(monkeypatch):
    monkeypatch.syspath_prepend(str(Path('scripts').resolve()))
    from audit_spark_funding_gap_events import GapEvents
    from report_allocation_capital_readiness import capital_timeline

    from settle.normalize.allocation_capital import ExternalFundingOperation as E

    day = date(2026, 8, 1)
    history = CapitalHistory((
        B('saver', day, 1, 'ethereum', 1, (M('venue', D(0), D(100)),),
          external_funding=(E('draw', 'saver', D(100)),)),
        B('income', day, 2, 'ethereum', 2, (M('cash', D(0), D(10), external_income=D(10)),)),
        B('repay', day, 3, 'ethereum', 3, (M('cash', D(10), D(-10)),),
          external_funding=(E('repay', 'saver', D(10)),)),
    ), {'S1': 'venue'}, {})
    records = []
    capital_timeline(history, day, day, on_batch=lambda b, values: records.append(
        json.dumps({'identity': b.identity, 'principal': {'S1': str(values['venue'])}})))
    audit = GapEvents(iter(records), {'S1': ['venue']}, day)
    observed = replay_history(history, day, day, funding_accounts={'venue'}, funding_observer=audit.observe)
    assert observed == replay_history(history, day, day, funding_accounts={'venue'})
    assert audit.result()['inception_contributions']['S1'] == {'repayment_effect': D(10), 'other_effect': D(0)}
