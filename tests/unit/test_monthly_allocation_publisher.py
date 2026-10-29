"""Isolated settlement controls receive the same provenance checks as reports."""
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def test_isolated_control_mismatch_is_rejected_before_build(tmp_path):
    spec = importlib.util.spec_from_file_location('publisher',
        Path(__file__).parents[2] / 'scripts/publish_monthly_allocations.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = '/isolated-controls/spark/2026-09/provenance.json'
    capital = tmp_path / 'capital.json.gz'
    capital.write_bytes(gzip.compress(json.dumps({'input_sha256': {source: 'a' * 64}}).encode()))
    yields = tmp_path / 'yields.json'
    yields.write_text(json.dumps({'input_sha256': {
        str(capital): hashlib.sha256(capital.read_bytes()).hexdigest(), source: 'b' * 64}}))
    with pytest.raises(ValueError, match='Revenue control hashes differ'):
        module.prepare(capital, yields)


def test_missing_control_lineage_cannot_bypass_publication_check(tmp_path):
    spec = importlib.util.spec_from_file_location('publisher_missing_control',
        Path(__file__).parents[2] / 'scripts/publish_monthly_allocations.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    capital = tmp_path / 'capital.json.gz'
    capital.write_bytes(gzip.compress(json.dumps({'input_sha256': {}}).encode()))
    yields = tmp_path / 'yields.json'
    yields.write_text(json.dumps({'input_sha256': {
        str(capital): hashlib.sha256(capital.read_bytes()).hexdigest()}}))
    with pytest.raises(ValueError, match='missing revenue control hashes'):
        module.prepare(capital, yields)
