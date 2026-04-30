"""Helper: assemble Spark Q1 2026 Dune fixtures from MCP-fetched rows.

Not a pipeline component. The actual MCP fetch happens via Claude Code +
mcp__dune tools — this just loads the captured JSON and reshapes it for the
runner. See `scripts/run_spark_2026_q1.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_debt() -> list[dict]:
    """Load the captured debt timeseries rows."""
    with open(HERE / "debt_timeseries.json") as f:
        return json.load(f)["rows"]


def load_ssr() -> list[dict]:
    """Reuse Grove's SSR fixture — SSR is Sky-wide, identical across primes."""
    grove_path = HERE.parent / "grove_2026_03" / "dune_outputs.json"
    with open(grove_path) as f:
        d = json.load(f)
    return d["ssr"]["rows"]
