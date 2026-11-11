#!/usr/bin/env python3
"""Read-only check of independent daily evidence against an MSC provenance file."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from settle.controls.assets import ROOT, validate_packet
from settle.domain.period import Month, Period
from settle.domain.primes import Chain


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provenance', type=Path, required=True)
    parser.add_argument('--controls', type=Path, required=True)
    parser.add_argument('--repo-root', type=Path, default=ROOT)
    args = parser.parse_args()
    payload = json.loads(args.provenance.read_text())
    pnl = SimpleNamespace(
        prime_id=payload['prime_id'], month=Month.parse(payload['month']),
        period=Period(date.fromisoformat(payload['period']['start']),
                      date.fromisoformat(payload['period']['end']),
                      {Chain(c): n for c, n in payload['pin_blocks_eom'].items()}),
        pin_blocks_som={Chain(c): n for c, n in payload['pin_blocks_som'].items()},
        sky_revenue_daily=payload['sky_revenue_daily'],
    )
    print(json.dumps(validate_packet(args.controls, pnl, repo_root=args.repo_root), indent=2))


if __name__ == '__main__':
    main()
