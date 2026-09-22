"""Versioned, compressed diagnostic snapshots of normalized capital history.

Replay state is never cached. Only immutable normalized inputs are saved, so a
replay fix can be tested without redoing every historical contract read.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from .allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

SCHEMA = 1


def fingerprint(prime, pins):
    from ..extract.input_cache import input_revision

    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    # Normalization, pricing, configuration and extract semantics invalidate
    # snapshots. Compute changes deliberately do not: replay is rerun fresh.
    for directory in ('normalize', 'extract', 'domain'):
        for path in sorted((root / directory).rglob('*.py')):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    digest.update(repr(prime).encode())
    digest.update(json.dumps({str(k): v for k, v in pins.items()}, sort_keys=True).encode())
    digest.update(str(input_revision()).encode())
    return digest.hexdigest()


def save_history(path, history, key):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    metadata = {'schema': SCHEMA, 'fingerprint': key,
                'venue_accounts': history.venue_accounts, 'unsupported': history.unsupported,
                'custody_accounts': history.custody_accounts, 'idle_accounts': sorted(history.idle_accounts)}
    try:
        with gzip.open(temporary, 'wt') as output:
            output.write(json.dumps(metadata) + '\n')
            for batch in history.batches:
                output.write(json.dumps(asdict(batch), default=str) + '\n')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_history(path, key):
    path = Path(path)
    if not path.exists():
        return None
    with gzip.open(path, 'rt') as source:
        metadata = json.loads(next(source))
        if metadata['schema'] != SCHEMA or metadata['fingerprint'] != key:
            return None
        batches = []
        for line in source:
            row = json.loads(line)
            row['day'] = date.fromisoformat(row['day'])
            row['minted'] = Decimal(row['minted'])
            row['minted_by_ilk'] = {k: Decimal(v) for k, v in row['minted_by_ilk'].items()}
            row['movements'] = tuple(AssetMovement(
                m['account'], Decimal(m['value_before']), Decimal(m['change']),
                Decimal(m['external_income']), m['preserve_basis']) for m in row['movements'])
            batches.append(CapitalBatch(**row))
    return CapitalHistory(tuple(batches), metadata['venue_accounts'], metadata['unsupported'],
                          metadata['custody_accounts'], set(metadata['idle_accounts']))
