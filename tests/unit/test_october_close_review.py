"""A stale or incomplete local review must stop the October batch preflight."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def close_review(tmp_path, monkeypatch):
    path = Path(__file__).parents[2] / "scripts/run_october_close.py"
    spec = importlib.util.spec_from_file_location("october_close", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    config = tmp_path / "config/spark.yaml"
    config.parent.mkdir()
    config.write_text("test configuration\n")
    directory = tmp_path / ".msc-transfer-reviews/spark/2026-10"
    directory.mkdir(parents=True)
    evidence = directory / "scan.json"
    evidence.write_text('{"test_evidence": true}\n')
    record = {"prime": "spark", "period": "2026-10", "status": "reviewed",
              "unclassified_count": 0,
              "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
              "evidence_hashes": {"scan.json": hashlib.sha256(evidence.read_bytes()).hexdigest()}}
    review = directory / "review.json"
    review.write_text(json.dumps(record))
    return module, record, review, config, evidence


def test_review_integrity_digest(close_review):
    module, _, review, _, _ = close_review
    assert module.check_review("spark") == hashlib.sha256(review.read_bytes()).hexdigest()


@pytest.mark.parametrize("field,value", [
    ("prime", "grove"), ("period", "2026-09"), ("status", "incomplete"),
    ("unclassified_count", 1), ("evidence_hashes", {}),
])
def test_incomplete_or_wrong_review_rejected(close_review, field, value):
    module, record, review, _, _ = close_review
    record[field] = value
    review.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        module.check_review("spark")


@pytest.mark.parametrize("changed", ["config", "evidence"])
def test_changed_inputs_invalidate_review(close_review, changed):
    module, _, _, config, evidence = close_review
    (config if changed == "config" else evidence).write_text("changed after review\n")
    with pytest.raises(ValueError):
        module.check_review("spark")
