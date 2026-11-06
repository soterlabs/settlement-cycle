"""Atomic local transaction-normalization chunks, never executable pickle data.

A chunk commits both output batches and the complete adapter state immediately
AFTER a transaction. Raw log/config/code identities must match on resume.
Cross-chain linking is deliberately rerun after restoring raw chain batches.
"""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from functools import cache
from pathlib import Path


@cache
def _classes():
    from ..domain.primes import Address, Chain, Token
    from ..extract.hypersync import LogRow
    from .allocation_capital import AssetMovement, CapitalBatch, ExternalFundingOperation

    return {
        c.__name__: c
        for c in (
            Address,
            Chain,
            Token,
            LogRow,
            AssetMovement,
            CapitalBatch,
            ExternalFundingOperation,
        )
    }


def encode(value):
    if isinstance(value, Enum):
        return {"enum": type(value).__name__, "value": value.value}
    if is_dataclass(value):
        if type(value).__name__ not in _classes():
            raise TypeError(f"Unsupported checkpoint class: {type(value).__name__}")
        return {
            "object": type(value).__name__,
            "fields": {f.name: encode(getattr(value, f.name)) for f in fields(value)},
        }
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, date):
        return {"date": value.isoformat()}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, dict):
        return {"dict": [[encode(k), encode(v)] for k, v in value.items()]}
    if isinstance(value, tuple):
        return {"tuple": [encode(x) for x in value]}
    if isinstance(value, set):
        return {"set": [encode(x) for x in sorted(value, key=repr)]}
    if isinstance(value, list):
        return [encode(x) for x in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"Unsupported checkpoint value: {type(value).__name__}")


def decode(value):
    if isinstance(value, list):
        return [decode(x) for x in value]
    if not isinstance(value, dict):
        return value
    if "object" in value:
        return _classes()[value["object"]](**{k: decode(v) for k, v in value["fields"].items()})
    if "enum" in value:
        return _classes()[value["enum"]](value["value"])
    if "dict" in value:
        return {decode(k): decode(v) for k, v in value["dict"]}
    if "tuple" in value:
        return tuple(decode(x) for x in value["tuple"])
    if "set" in value:
        return {decode(x) for x in value["set"]}
    if "decimal" in value:
        return Decimal(value["decimal"])
    if "date" in value:
        return date.fromisoformat(value["date"])
    if "bytes" in value:
        return bytes.fromhex(value["bytes"])
    raise ValueError("Invalid checkpoint encoding")


def groups_digest(groups):
    digest = hashlib.sha256()
    for identity, logs in groups:
        digest.update(
            json.dumps(encode((identity, logs)), sort_keys=True, separators=(",", ":")).encode()
        )
        digest.update(b"\n")
    return digest.hexdigest()


def atomic_write(path, raw):
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


class Checkpoints:
    def __init__(self, root, identity, chunk_size=1000):
        if chunk_size <= 0:
            raise ValueError("Checkpoint chunk size must be positive")
        self.root, self.identity, self.chunk_size = Path(root), identity, chunk_size
        self.manifests = {}

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield self
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def load(self, chain, groups):
        directory = self.root / chain
        directory.mkdir(exist_ok=True)
        identity = dict(
            schema=1, source=self.identity, logs=groups_digest(groups), count=len(groups)
        )
        path = directory / "manifest.json"
        manifest = (
            json.loads(path.read_text()) if path.exists() else dict(identity=identity, chunks=[])
        )
        if manifest["identity"] != identity:
            raise ValueError("Normalization checkpoint inputs changed; use a new directory")
        cursor, batches, state = 0, [], None
        for chunk in manifest["chunks"]:
            if chunk["start"] != cursor or not cursor < chunk["end"] <= len(groups):
                raise ValueError("Invalid checkpoint transaction coverage")
            filename = chunk["file"]
            if Path(filename).name != filename:
                raise ValueError("Invalid checkpoint chunk path")
            raw = (directory / filename).read_bytes()
            if hashlib.sha256(raw).hexdigest() != chunk["sha256"]:
                raise ValueError("Normalization checkpoint checksum mismatch")
            saved = decode(json.loads(gzip.decompress(raw)))
            if len(saved["batches"]) != chunk["end"] - cursor:
                raise ValueError("Checkpoint batch count mismatch")
            for batch, ((block, tx), _) in zip(
                saved["batches"], groups[cursor : chunk["end"]], strict=True
            ):
                if (batch.chain, batch.block, batch.identity) != (chain, block, f"{chain}:{tx}"):
                    raise ValueError("Checkpoint transaction identity mismatch")
            batches.extend(saved["batches"])
            state = saved["state"]
            cursor = chunk["end"]
        self.manifests[chain] = manifest
        return cursor, batches, state

    def save(self, chain, end, batches, state):
        manifest = self.manifests[chain]
        start = manifest["chunks"][-1]["end"] if manifest["chunks"] else 0
        if end <= start or len(batches) != end - start:
            raise ValueError("Invalid checkpoint commit range")
        raw = gzip.compress(
            json.dumps(encode(dict(batches=batches, state=state)), separators=(",", ":")).encode(),
            mtime=0,
        )
        filename = f"{start:09d}-{end:09d}.json.gz"
        directory = self.root / chain
        atomic_write(directory / filename, raw)
        next_manifest = dict(
            manifest,
            chunks=[
                *manifest["chunks"],
                dict(start=start, end=end, file=filename, sha256=hashlib.sha256(raw).hexdigest()),
            ],
        )
        atomic_write(directory / "manifest.json", json.dumps(next_manifest, indent=2).encode())
        self.manifests[chain] = next_manifest


def capture_state(units, queues, managers, psm, nft, async_vaults, custody, burns, verified):
    return dict(
        units=units,
        queues=queues,
        managers=managers,
        psm_shares=psm.shares,
        nft={key: getattr(nft, key) for key in ("liquidity", "owed", "custody_accounts")},
        async_state={
            key: getattr(async_vaults, key)
            for key in ("metadata", "active_vaults", "pending", "custody_accounts", "assets")
        },
        custody=custody,
        burns=burns,
        verified=verified,
    )


def restore_state(state, units, queues, managers, psm, nft, async_vaults, custody, burns, verified):
    for target, values in (
        (units, state["units"]),
        (queues, state["queues"]),
        (managers, state["managers"]),
        (custody, state["custody"]),
    ):
        target.clear()
        target.update(values)
    psm.shares = state["psm_shares"]
    for obj, values in ((nft, state["nft"]), (async_vaults, state["async_state"])):
        for key, value in values.items():
            target = getattr(obj, key)
            target.clear()
            target.update(value)
    burns[:] = state["burns"]
    verified.clear()
    verified.update(state["verified"])
