"""Verify complete calculations in fresh processes using persistent inputs.

Run with ``python -m settle.revenue.verification --as-of YYYY-MM-DD --output DIR``.
Reports contain no provider URLs, tokens or raw responses. Every HTTP attempt is
counted at requests' transport boundary, including retries and swallowed errors.
"""
from __future__ import annotations

import argparse
import contextvars
import dataclasses
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

import requests

PRIMES = ("grove", "spark", "obex", "keel", "skybase", "osero")
_certifying = contextvars.ContextVar("revenue_certification", default=False)


def canonical(value):
    """Lossless, deterministic representation of every calculation field."""
    if dataclasses.is_dataclass(value):
        return {f.name: canonical(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(getattr(k, "value", k)): canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if hasattr(value, "value"):
        return canonical(value.value)
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


class ProviderAudit:
    def __init__(self):
        self.calls = []
        self.dune_attempts = 0

    def __enter__(self):
        from settle.extract import dune
        from settle.normalize.sources.hypersync_block_resolver import HyperSyncBlockResolver
        self.stack = ExitStack()
        original_send = requests.Session.send
        original_validate = HyperSyncBlockResolver.validate_finalized_boundary
        audit = self

        def certify(*args, **kwargs):
            token = _certifying.set(True)
            try:
                return original_validate(*args, **kwargs)
            finally:
                _certifying.reset(token)

        def forbidden(*args, **kwargs):
            audit.dune_attempts += 1
            raise RuntimeError("Dune is forbidden during daily revenue verification")

        def send(session, request, **kwargs):
            try:
                body = json.loads(request.body or "{}")
            except (ValueError, TypeError):
                body = {}
            host = urlparse(request.url).hostname or ""
            hs = host.endswith(".hypersync.xyz")
            category = "historical"
            if hs and body == {"from_block": 0, "to_block": 1, "logs": [],
                               "field_selection": {"block": ["number"]}}:
                category = "head"
            elif hs and _certifying.get() and body.get("include_all_blocks") and not body.get("logs"):
                category = "boundary"
            event = {"provider": "hypersync" if hs else "rpc" if "method" in body else "other",
                     "category": category, "request": digest(body), "bytes": 0,
                     "method": body.get("method"), "status": None}
            audit.calls.append(event)
            response = original_send(session, request, **kwargs)
            event.update(bytes=len(response.content), status=response.status_code)
            return response

        self.stack.enter_context(patch.object(requests.Session, "send", send))
        self.stack.enter_context(patch.object(HyperSyncBlockResolver, "validate_finalized_boundary", certify))
        original = dune.execute_query
        for module in list(sys.modules.values()):
            if module and getattr(module, "__name__", "").startswith("settle."):
                for name, value in list(vars(module).items()):
                    if value is original:
                        self.stack.enter_context(patch.object(module, name, forbidden))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


def calculate(prime, cutoff):
    from settle.compute import compute_monthly_pnl
    from settle.domain.config import load_prime_by_id
    from settle.domain.period import Month
    started = time.monotonic()
    with ProviderAudit() as audit:
        result = compute_monthly_pnl(load_prime_by_id(prime), Month(cutoff.year, cutoff.month), as_of=cutoff)
    if audit.dune_attempts:
        raise RuntimeError(f"Calculation attempted {audit.dune_attempts} Dune calls")
    return {"prime": prime, "cutoff": cutoff.isoformat(), "result": canonical(result),
            "calls": audit.calls, "elapsed_seconds": time.monotonic() - started,
            "dune_attempts": audit.dune_attempts}


def worker(prime, cutoff, output, *, timeout=21600):
    """A new interpreter and empty local cache for every run."""
    with tempfile.TemporaryDirectory(prefix="revenue-worker-") as cache:
        env = dict(os.environ, SETTLE_REQUIRE_POSTGRES="1", SETTLE_NO_CACHE="0",
                   HYPERSYNC_NO_STORE="0", SETTLE_CACHE_DIR=cache)
        subprocess.run([sys.executable, "-m", "settle.revenue.verification", "--worker", "--prime", prime,
                        "--as-of", cutoff.isoformat(), "--output", str(output)],
                       env=env, check=True, timeout=timeout)
    return json.loads(output.read_text())


def compare(first, second):
    historical = [c for c in second["calls"] if c["category"] not in {"head", "boundary"}]
    same_identity = (first["prime"], first["cutoff"]) == (second["prime"], second["cutoff"])
    return {"prime": first["prime"], "cutoff": first["cutoff"],
            "same_identity": same_identity,
            "matched": first["result"] == second["result"],
            "historical_requests": len(historical), "unexpected_requests": historical,
            "first_sha256": digest(first["result"]), "second_sha256": digest(second["result"]),
            "passed": same_identity and first["result"] == second["result"] and not historical
                      and not first["dune_attempts"] and not second["dune_attempts"]}


def main():
    from dotenv import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--prime", choices=PRIMES, action="append")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not os.environ.get("DATABASE_URL"):
        parser.error("DATABASE_URL is required")
    if args.worker:
        if not args.prime or len(args.prime) != 1:
            parser.error("worker requires one prime")
        args.output.write_text(json.dumps(calculate(args.prime[0], args.as_of), sort_keys=True))
        return
    args.output.mkdir(parents=True, exist_ok=True)
    reports = []
    for prime in args.prime or PRIMES:
        first = worker(prime, args.as_of, args.output / f"{prime}-first.json")
        second = worker(prime, args.as_of, args.output / f"{prime}-second.json")
        reports.append(compare(first, second))
        (args.output / "verification.json").write_text(json.dumps(reports, indent=2))
    if not all(r["passed"] for r in reports):
        raise SystemExit("Same-date reuse verification failed; inspect verification.json")


if __name__ == "__main__":
    main()
