"""The retained replay works in shallow checkouts without weakening source seals."""
import subprocess

import pytest

from scripts import verify_october_retained_after_pr282 as verifier


def test_full_ordinary_replay_needs_no_historical_commit_or_main_ref(monkeypatch):
    original = subprocess.check_output

    def shallow(command, *args, **kwargs):
        assert verifier.OLD_HEAD not in ' '.join(command)
        assert command[1] not in {'show', 'diff'}
        if command[-1] == 'origin/main':
            raise subprocess.CalledProcessError(128, command)
        return original(command, *args, **kwargs)

    monkeypatch.setattr(verifier.subprocess, 'check_output', shallow)
    result = verifier.replay()
    assert result['main_commit'] is None
    assert len(result['primes']) == 6
    assert sum(row['days_replayed'] for row in result['primes'].values()) == 186
    assert result['primes']['spark']['send'] == '3938958'  # Ordinary, before historical corrections.
    assert result['canonical_reports_written'] is False


def test_source_seal_detects_unapproved_financial_code_changes(monkeypatch):
    original = subprocess.check_output

    def changed(command, *args, **kwargs):
        raw = original(command, *args, **kwargs)
        if command[1] == 'ls-tree':
            rows = raw.split(b'\0')
            for index, row in enumerate(rows):
                if row.endswith(b'\tsrc/settle/compute/sky_revenue.py'):
                    rows[index] = b'100644 blob ' + b'0' * 40 + b'\tsrc/settle/compute/sky_revenue.py'
            return b'\0'.join(rows)
        return raw

    monkeypatch.setattr(verifier.subprocess, 'check_output', changed)
    with pytest.raises(ValueError, match='Financial/configuration changes'):
        verifier.replay()


def test_source_seal_cannot_be_silently_replaced(tmp_path, monkeypatch):
    source = verifier.ROOT / 'reconciliation/2026-10/retained-source-seal.json'
    target = tmp_path / 'reconciliation/2026-10/retained-source-seal.json'
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes() + b' ')
    monkeypatch.setattr(verifier, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='Original source seal changed'):
        verifier.load_source_seal()
