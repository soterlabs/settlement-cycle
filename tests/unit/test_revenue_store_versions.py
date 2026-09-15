from unittest.mock import patch

import pytest

from settle.revenue import store


def test_untracked_source_file_cannot_be_published_as_clean_revision(monkeypatch):
    for key in ('REVENUE_CODE_VERSION', 'RAILWAY_GIT_COMMIT_SHA', 'GITHUB_SHA'):
        monkeypatch.delenv(key, raising=False)
    with patch.object(store.subprocess, 'check_output', return_value='?? src/untracked.py\n'):
        with pytest.raises(ValueError, match='dirty'):
            store.capture_versions()


def test_explicit_deployment_version_and_input_revision(monkeypatch):
    monkeypatch.setenv('REVENUE_CODE_VERSION', 'deployment-sha')
    monkeypatch.setenv('SETTLE_INPUT_REVISION', 'corrected')
    version = store.capture_versions()
    assert version.code == 'deployment-sha'
    assert version.inputs == 'corrected'
    assert len(version.configuration) == 64
