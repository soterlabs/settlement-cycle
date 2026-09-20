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
import threading
import time
from contextlib import ExitStack
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

import requests

PRIMES = ("grove", "spark", "obex", "keel", "skybase", "osero")
_certifying = contextvars.ContextVar("revenue_certification", default=False)
_refreshing_rates = contextvars.ContextVar("revenue_reference_refresh", default=False)


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
        self.cache = {"postgres_hits": 0, "postgres_misses": 0}
        self.lock = threading.Lock()

    def __enter__(self):
        from settle.extract import dune, postgres_store
        from settle.extract.publication import transport_failure, transport_rpc_response
        from settle.normalize.sources.hypersync_block_resolver import HyperSyncBlockResolver
        self.stack = ExitStack()
        original_get = postgres_store.get
        original_send = requests.Session.send
        original_validate = HyperSyncBlockResolver.validate_finalized_boundary
        audit = self

        def get(*args, **kwargs):
            result = original_get(*args, **kwargs)
            with audit.lock:
                audit.cache["postgres_misses" if result is postgres_store.MISS else "postgres_hits"] += 1
            return result

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
            from .reference_rates import SOURCE
            if _refreshing_rates.get() and request.url.split('?')[0] == SOURCE:
                category = 'reference_refresh'
            if hs and body == {"from_block": 0, "to_block": 1, "logs": [],
                               "field_selection": {"block": ["number"]}}:
                category = "head"
            elif hs and _certifying.get() and body.get("include_all_blocks") and not body.get("logs"):
                category = "boundary"
            event = {"provider": "hypersync" if hs else "rpc" if "method" in body else "other",
                     "category": category, "request": digest(body), "bytes": 0,
                     "method": body.get("method"), "status": None,
                     "chain": host.split(".")[0] if hs else None,
                     "log_range": [body.get("from_block"), body.get("to_block")]
                         if hs and body.get("logs") else None}
            audit.calls.append(event)
            started = time.monotonic()
            try:
                response = original_send(session, request, **kwargs)
                event.update(bytes=len(response.content), status=response.status_code)
                if response.status_code >= 400:
                    transport_failure(requests.HTTPError('Upstream HTTP failure'))
                if event['provider'] == 'rpc':
                    transport_rpc_response(response)
                return response
            except requests.RequestException as exc:
                transport_failure(exc)
                raise
            finally:
                event["seconds"] = time.monotonic() - started

        self.stack.enter_context(patch.object(postgres_store, "get", get))
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

    from .metrics import ExtractionTimer
    started = time.monotonic()
    from settle.extract.publication import PublicationGuard
    from settle.store.db import connect
    from . import reference_rates, store
    config = load_prime_by_id(prime)
    reference_started = time.monotonic()
    token = _refreshing_rates.set(True)
    try:
        with ProviderAudit() as reference_audit, connect(autocommit=True) as conn:
            store.apply_schema(conn)
            prepared = reference_rates.prepare(conn, config, cutoff)
    finally:
        _refreshing_rates.reset(token)
    reference_seconds = time.monotonic() - reference_started
    kwargs = {'reference_rate_history': prepared.history} if prepared else {}
    with PublicationGuard(), ExtractionTimer() as timer, ProviderAudit() as audit:
        result = compute_monthly_pnl(config, Month(cutoff.year, cutoff.month), as_of=cutoff,
                                     include_allocation_financing=True, **kwargs)
    dune_attempts = audit.dune_attempts + reference_audit.dune_attempts
    if dune_attempts:
        raise RuntimeError(f"Calculation attempted {dune_attempts} Dune calls")
    elapsed = time.monotonic() - started
    return {"prime": prime, "cutoff": cutoff.isoformat(), "result": canonical(result),
            "cache": audit.cache, "extraction_seconds": timer.seconds + reference_seconds,
            "reference_refresh_seconds": reference_seconds,
            "calculation_seconds": max(0, elapsed - timer.seconds - reference_seconds),
            "calls": reference_audit.calls + audit.calls, "elapsed_seconds": elapsed,
            "reference_snapshot": prepared.snapshot_id if prepared else None,
            "dune_attempts": dune_attempts}


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
    historical = [c for c in second["calls"] if c["category"] not in {"head", "boundary", "reference_refresh"}]
    same_identity = (first["prime"], first["cutoff"]) == (second["prime"], second["cutoff"])
    return {"prime": first["prime"], "cutoff": first["cutoff"],
            "same_identity": same_identity,
            "matched": first["result"] == second["result"],
            "same_reference_snapshot": first.get("reference_snapshot") == second.get("reference_snapshot"),
            "historical_requests": len(historical), "unexpected_requests": historical,
            "first_sha256": digest(first["result"]), "second_sha256": digest(second["result"]),
            "passed": same_identity and first["result"] == second["result"]
                      and first.get("reference_snapshot") == second.get("reference_snapshot") and not historical
                      and not first["dune_attempts"] and not second["dune_attempts"]}


def summarize(report):
    calls = report["calls"]
    return {"cutoff": report["cutoff"], "requests": len(calls),
            "requests_by_provider": {p: sum(c["provider"] == p for c in calls)
                                     for p in ("hypersync", "rpc", "other")},
            "rpc_requests_by_method": {method: sum(c.get('method') == method for c in calls)
                                       for method in sorted({c['method'] for c in calls if c.get('method')})},
            "reference_refresh_requests": sum(c['category'] == 'reference_refresh' for c in calls),
            "response_bytes": sum(c["bytes"] for c in calls),
            "historical_requests": sum(c["category"] == "historical" for c in calls),
            "elapsed_seconds": report["elapsed_seconds"],
            "extraction_seconds": report["extraction_seconds"],
            "calculation_seconds": report["calculation_seconds"], "cache": report["cache"]}


def validate_window(cutoff, advance=False, today=None):
    today = today or datetime.now(UTC).date()
    last = cutoff + timedelta(days=1) if advance else cutoff
    if cutoff < today - timedelta(days=90) or last >= today:
        raise ValueError("cutoffs must be within the last 90 completed UTC days")


def main():
    from dotenv import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--prime", choices=PRIMES, action="append")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--advance", action="store_true", help="Also measure the next completed UTC day")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        validate_window(args.as_of, args.advance)
    except ValueError as exc:
        parser.error(str(exc))
    if not os.environ.get("DATABASE_URL"):
        parser.error("DATABASE_URL is required")
    if args.worker:
        if not args.prime or len(args.prime) != 1:
            parser.error("worker requires one prime")
        args.output.write_text(json.dumps(calculate(args.prime[0], args.as_of), sort_keys=True))
        return
    args.output.mkdir(parents=True, exist_ok=True)
    primes = args.prime or PRIMES
    # Complete the baseline fleet before advancing any prime: a next-day run
    # must not prewarm shared future inputs for another prime's baseline.
    first = {prime: worker(prime, args.as_of, args.output / f"{prime}-first.json") for prime in primes}
    reports = []
    for prime in primes:
        second = worker(prime, args.as_of, args.output / f"{prime}-second.json")
        report = compare(first[prime], second)
        report["measurements"] = {"first": summarize(first[prime]), "same_date": summarize(second)}
        reports.append(report)
        (args.output / "verification.json").write_text(json.dumps(reports, indent=2))
    if args.advance and all(r["passed"] for r in reports):
        for report in reports:
            prime = report['prime']
            advanced = worker(prime, args.as_of + timedelta(days=1), args.output / f"{prime}-next.json")
            report["measurements"]["next_day"] = summarize(advanced)
            (args.output / "verification.json").write_text(json.dumps(reports, indent=2))
    if not all(r["passed"] for r in reports):
        raise SystemExit("Same-date reuse verification failed; inspect verification.json")


if __name__ == "__main__":
    main()
