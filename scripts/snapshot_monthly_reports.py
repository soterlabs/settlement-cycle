"""Capture monthly source bytes at a reviewed commit using authenticated gh.

No daily pipeline, RPC, database, publication or deployment is performed.
Usage: python scripts/snapshot_monthly_reports.py --commit <40 hex> --output <new.json>
"""
import argparse
import base64
import hashlib
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def gh(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch('[a-f0-9]{40}', args.commit):
        parser.error('commit must be an immutable full Git SHA')
    repo = 'soterlabs/settlement-reports'
    tree = gh(f'repos/{repo}/git/trees/{args.commit}?recursive=1')
    if tree.get('truncated'):
        raise ValueError('Truncated source tree')
    entries = [entry for entry in tree['tree'] if entry['type'] == 'blob' and re.fullmatch(
        r'reports/(spark|grove|obex|keel|skybase|osero)/\d{4}-\d{2}/summary\.md', entry['path'])]
    if not entries:
        raise ValueError('No monthly summaries found')

    def fetch(entry):
        blob = gh(f"repos/{repo}/git/blobs/{entry['sha']}")
        if blob['encoding'] != 'base64':
            raise ValueError('Unsupported Git blob encoding')
        raw = base64.b64decode(blob['content'])
        actual = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        if actual != entry['sha']:
            raise ValueError('Git blob hash mismatch')
        return {'path': entry['path'], 'git_blob_sha': actual,
                'sha256': hashlib.sha256(raw).hexdigest(), 'text': raw.decode('utf-8')}

    with ThreadPoolExecutor(max_workers=5) as executor:
        reports = sorted(executor.map(fetch, entries), key=lambda report: report['path'])
    snapshot = {'schema_version': 'published-settlement-summaries.v1', 'repository': repo,
                'commit': args.commit, 'reports': reports}
    with args.output.open('x') as destination:
        json.dump(snapshot, destination, ensure_ascii=False, indent=2)
        destination.write('\n')
    print(f'Saved {len(reports)} monthly summaries to {args.output}')


if __name__ == '__main__':
    main()
