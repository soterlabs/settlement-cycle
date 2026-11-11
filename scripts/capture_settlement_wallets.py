#!/usr/bin/env python3
"""Capture independent native-USDS wallet evidence; never issues a passing review."""
import argparse
import json
import logging
from pathlib import Path

from dotenv import load_dotenv

from settle.controls.boundaries import capture_genesis
from settle.controls.bridges import capture_bridges
from settle.controls.wallets import capture_wallets
from settle.domain.config import load_prime_by_id
from settle.domain.period import Month, Period
from settle.domain.primes import Chain


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prime', required=True)
    parser.add_argument('--pins', type=Path, required=True, help='JSON {UTC-day: {chain: EOD-block}}')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--month', help='Also capture gateway histories for this UTC month')
    parser.add_argument('--bridge-output', type=Path, help='Required with --month')
    parser.add_argument('--genesis-output', type=Path, help='Retain linked genesis/first-block evidence for zero pins')
    args = parser.parse_args()
    if bool(args.month) != bool(args.bridge_output):
        parser.error('--month and --bridge-output must be supplied together')
    outputs = [p for p in (args.output, args.bridge_output, args.genesis_output) if p is not None]
    if len({p.resolve() for p in outputs}) != len(outputs):
        parser.error('Evidence outputs must use distinct paths')
    if any(p.exists() or p.is_symlink() for p in outputs):
        parser.error('Evidence outputs already exist; use new paths')
    load_dotenv()
    # Existing transport retry logs may contain credential-bearing RPC URLs.
    logging.getLogger('settle.extract.rpc').disabled = True
    try:
        prime = load_prime_by_id(args.prime)
        pins = json.loads(args.pins.read_text())
        if any(block == 0 for row in pins.values() for block in row.values()) and not args.genesis_output:
            parser.error('Zero pins require --genesis-output')
        if args.genesis_output:
            genesis = capture_genesis(prime, pins)
        result = capture_wallets(prime, pins)
        if args.month:
            month = Month.parse(args.month)
            period = Period.from_month(month, {Chain(c): n for c, n in pins[str(month.last_day)].items()})
            bridge = capture_bridges(prime, period)
    except Exception as exc:
        raise SystemExit(f'Independent wallet capture failed ({type(exc).__name__}); no output written') from None
    with args.output.open('x') as output:
        json.dump(result, output, indent=2)
    if args.bridge_output:
        with args.bridge_output.open('x') as output:
            json.dump(bridge, output, indent=2)
    if args.genesis_output:
        with args.genesis_output.open('x') as output:
            json.dump(genesis, output, indent=2)
    print(f'Captured {len(result["states"])} wallet observations; full inventory review still required')


if __name__ == '__main__':
    main()
