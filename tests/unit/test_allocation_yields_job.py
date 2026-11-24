"""Automated monthly yields: close precondition, verification gate, atomic store."""
import hashlib
import importlib.util
import json
import shutil
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


job = script('allocation_yields_job')
REC = ROOT / 'reconciliation'


def test_months_cover_completed_months_only():
    assert job.months('2026-10', date(2026, 11, 24)) == ['2026-10']
    assert job.months('2026-11', date(2026, 11, 30)) == []
    assert job.months('2026-11', date(2027, 2, 1)) == ['2026-11', '2026-12', '2027-01']
    assert job.month_end('2026-12') == date(2026, 12, 31)
    assert job.month_end('2026-02') == date(2026, 2, 28)


def test_close_control_requires_the_committed_finalization(tmp_path):
    assert job.close_control(ROOT, 'obex', '2026-10')[0] == ROOT / job.CONTROLS / 'obex/2026-10/provenance.json'
    assert job.close_control(ROOT, 'obex', '2031-01') == (None, 'waiting_for_close')
    # A control that differs from the committed close is an error, not a wait.
    control = tmp_path / job.CONTROLS / 'obex/2026-10/provenance.json'
    control.parent.mkdir(parents=True)
    control.write_text('{}')
    (tmp_path / 'reconciliation/2026-10').mkdir(parents=True)
    shutil.copy(REC / '2026-10/finalization.json', tmp_path / 'reconciliation/2026-10/finalization.json')
    with pytest.raises(ValueError, match='does not match the committed close'):
        job.close_control(tmp_path, 'obex', '2026-10')
    # Finalized without this prime's provenance -> still waiting.
    assert job.close_control(tmp_path, 'nobody', '2026-10') == (None, 'waiting_for_close')


def october_obex_pipeline(tamper=None):
    """Stand-in for the capture/report steps: the committed October artifacts."""
    def pipeline(root, prime, month, control, work):
        (work / 'inputs').mkdir(parents=True, exist_ok=True)
        for name in ('history.jsonl.gz', 'idle_deductions.json', 'pins.json'):
            shutil.copy(REC / 'monthly-allocation-inputs/obex/2026-10' / name, work / 'inputs' / name)
        shutil.copy(REC / 'allocation_capital_readiness_obex_2026_10.json.gz', work / 'readiness.json.gz')
        yields = json.loads((REC / 'allocation_yields_obex_2026_10.json').read_text())
        if tamper:
            tamper(yields)
        (work / 'yields.json').write_text(json.dumps(yields))
        for name in ('readiness.md', 'yields.md'):
            (work / name).write_text('report\n')
        documents = job._publisher(root).prepare(work / 'readiness.json.gz', work / 'yields.json')
        (work / 'preview.json').write_text(json.dumps(documents, indent=2) + '\n')
    return pipeline


class FakeConn:
    def __init__(self, log):
        self.log = log

    @contextmanager
    def transaction(self):
        self.log.append('begin')
        yield
        self.log.append('commit')

    def execute(self, sql, params=None):
        self.log.append((' '.join(sql.split())[:40], params))


def run(tmp_path, *, published=None, pipeline=None, check=False):
    log = []

    @contextmanager
    def connect():
        yield FakeConn(log)

    report = job.process(ROOT, 'obex', '2026-10', tmp_path, connect=connect, code_version='abc',
                         check_published=check, pipeline=pipeline or october_obex_pipeline(),
                         read=lambda conn, prime, month: published)
    return report, log


def test_publishes_verified_document_and_artifacts_in_one_transaction(tmp_path):
    report, log = run(tmp_path)
    assert report['status'] == 'published' and report['rows'] == 1
    inserts = [entry for entry in log if isinstance(entry, tuple) and entry[0].startswith('INSERT INTO allocation')]
    assert inserts[0][0].startswith('INSERT INTO allocation_monthly_results')
    artifacts = {params[1]: params for _, params in inserts[1:]}
    assert set(artifacts) == set(job.ARTIFACTS)
    history = (REC / 'monthly-allocation-inputs/obex/2026-10/history.jsonl.gz').read_bytes()
    assert artifacts['inputs/history.jsonl.gz'][2] == hashlib.sha256(history).hexdigest()
    assert all(p[0] == report['revision_id'] and p[5] == 'abc' for p in artifacts.values())
    assert log[0] == 'begin' and log[-1] == 'commit'
    assert not (tmp_path / 'obex/2026-10').exists()  # work dir removed only after the store


def test_already_published_or_unclosed_months_do_nothing(tmp_path):
    def never(*args):
        raise AssertionError('pipeline must not run')

    report, _ = run(tmp_path, published={'revision_id': 'r1'}, pipeline=never)
    assert report == {'prime': 'obex', 'month': '2026-10', 'status': 'already_published', 'revision_id': 'r1'}

    @contextmanager
    def connect():
        yield None

    report = job.process(ROOT, 'obex', '2031-01', tmp_path, connect=connect, code_version='abc',
                         pipeline=never, read=lambda *a: None)
    assert report['status'] == 'waiting_for_close'


def test_failed_independent_check_blocks_publication(tmp_path):
    work = tmp_path / 'work'
    work.mkdir()
    october_obex_pipeline()(ROOT, 'obex', '2026-10', None, work)
    control = json.loads((ROOT / job.CONTROLS / 'obex/2026-10/provenance.json').read_text())
    job.checked_document(ROOT, 'obex', '2026-10', ROOT / job.CONTROLS / 'obex/2026-10/provenance.json', work)

    bad_revenue = json.loads(json.dumps(control))
    bad_revenue['venue_breakdown'][0]['actual_revenue'] = '1'
    extra_venue = json.loads(json.dumps(control))
    extra_venue['venue_breakdown'].append({**control['venue_breakdown'][0], 'venue_id': 'V99'})
    for case, tampered in (('gross', bad_revenue), ('V99', extra_venue)):
        path = tmp_path / f'{case}.json'
        path.write_text(json.dumps(tampered))
        with pytest.raises(ValueError, match='independent verification failed') as err:
            job.checked_document(ROOT, 'obex', '2026-10', path, work)
        assert case in str(err.value)


def test_unreproducible_preview_is_rejected(tmp_path):
    work = tmp_path / 'work'
    work.mkdir()
    october_obex_pipeline()(ROOT, 'obex', '2026-10', None, work)
    (work / 'preview.json').write_text('[]\n')
    with pytest.raises(ValueError, match='not reproducible'):
        job.checked_document(ROOT, 'obex', '2026-10', ROOT / job.CONTROLS / 'obex/2026-10/provenance.json', work)


def test_check_published_compares_rows_and_never_writes(tmp_path):
    pipeline = october_obex_pipeline()
    probe = tmp_path / 'probe'
    probe.mkdir()
    pipeline(ROOT, 'obex', '2026-10', None, probe)
    published = json.loads((probe / 'preview.json').read_text())[0]
    report, log = run(tmp_path / 'same', published=published, check=True)
    assert report['status'] == 'matches_published' and log == []
    changed = json.loads(json.dumps(published))
    changed['allocations'][0]['net_apy'] = '0'
    report, _ = run(tmp_path / 'diff', published=changed, check=True)
    assert report['status'] == 'differs' and report['venues_differing'] == [changed['allocations'][0]['venue_id']]
