"""MSC #14 must present full historical adjustments once and match reports."""
import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def draft(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    return importlib.import_module('build_october_forum_draft')


def test_post_matches_reports_and_separates_reconciliation_and_partner_rebate(draft):
    text = draft.build()
    assert text == (ROOT / 'forum_posts/msc-2026-10.md').read_text()
    assert '# MSC #14' in text
    assert 'Partner Rebate:** 810,713.50 USDS to Grove; 1,246,289.50 USDS to Sky' in text
    assert text.count('Send-only refund: +5,514.00') == 1
    assert 'Sky supply-side correction: -110,419.56' in text
    assert '-31,889.50' not in text  # Do not show only the increment still to configure.
    assert 'Prime supply-side correction: -273,508.90' in text
    assert '15,812 USDS' in text and '42,396 USDS' in text
    assert '**Pending:**' not in text
    assert '**Sky net revenue**' in text
    assert '90-day SKY vest stream total' in text
    assert 'JAAA' not in text and 'JTRSY' not in text


def test_stale_reconciliation_cannot_silently_change_forum_amounts(draft, monkeypatch):
    original = draft.replacements

    def changed(ledger):
        values = original(ledger)
        values['grove']['sv_adj'] = '999'
        return values

    monkeypatch.setattr(draft, 'replacements', changed)
    with pytest.raises(ValueError, match='Adjustment differs from reconciliation: grove'):
        draft.build()


def test_stale_preview_report_hash_fails(draft, monkeypatch):
    original = json.loads

    def changed(value, *args, **kwargs):
        result = original(value, *args, **kwargs)
        if isinstance(result, dict) and result.get('status') == 'derived_unexecuted_prime_legs':
            result['rows'][0]['summary_sha256'] = 'wrong'
        return result

    monkeypatch.setattr(draft.json, 'loads', changed)
    with pytest.raises(ValueError, match='Stale summary: spark'):
        draft.build()


def test_changed_treasury_input_rejects_stale_forum(draft, monkeypatch):
    original = json.loads

    def changed(value, *args, **kwargs):
        result = original(value, *args, **kwargs)
        if isinstance(result, dict) and 'sha256' in result and 'settlements/tmf/2026-10/summary.md' in result['sha256']:
            result['sha256']['settlements/tmf/2026-10/summary.md'] = 'wrong'
        return result

    monkeypatch.setattr(draft.json, 'loads', changed)
    with pytest.raises(ValueError, match='Stale treasury evidence'):
        draft.build()
