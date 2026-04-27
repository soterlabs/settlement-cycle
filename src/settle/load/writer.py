"""Top-level settlement writer + default output-path resolver."""

from __future__ import annotations

import os
from pathlib import Path

from ..domain.monthly_pnl import MonthlyPnL
from .csv import write_csv, write_venues_csv
from .markdown import write_markdown
from .provenance import write_provenance

# settlement-cycle/src/settle/load/writer.py → parents[3] = settlement-cycle/
_REPO_ROOT = Path(__file__).resolve().parents[3]


def default_output_dir(prime_id: str, month: str) -> Path:
    """Resolve ``<repo>/settlements/<prime_id>/<month>/``.

    Settlement artifacts land inside this repo so the implementation is
    self-contained — no dependency on a sibling clone.

    Resolution order:
    1. ``SETTLE_OUTPUT_DIR`` env var (treated as the *root*; ``/<prime>/<month>/``
       is appended automatically).
    2. ``<repo>/settlements/`` (default).
    """
    base = Path(os.environ["SETTLE_OUTPUT_DIR"]).expanduser() if "SETTLE_OUTPUT_DIR" in os.environ \
        else _REPO_ROOT / "settlements"
    return base / prime_id / month


def write_settlement(
    pnl: MonthlyPnL,
    output_dir: Path,
    *,
    sources: dict[str, str] | None = None,
) -> dict[str, Path]:
    """Write all four artifacts (markdown, csv, venues.csv, provenance.json).

    Returns a dict mapping artifact name → written file path. ``venues.csv`` is
    only present if the prime has at least one venue.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {
        "markdown":   write_markdown(pnl, output_dir / "pnl.md"),
        "csv":        write_csv(pnl, output_dir / "pnl.csv"),
        "provenance": write_provenance(pnl, output_dir / "provenance.json", sources=sources),
    }
    venues_path = write_venues_csv(pnl, output_dir / "venues.csv")
    if venues_path is not None:
        written["venues_csv"] = venues_path
    return written
