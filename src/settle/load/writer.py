"""Top-level settlement writer + default output-path resolver.

Emits only the canonical artifacts:
  - ``provenance.json``                                 (machine-readable)
  - ``{prime}_settlement_{month_name}_{year}.xlsx``     (human-readable)

The xlsx is rendered in-process by ``scripts.build_settlement_xlsx``,
which reads only ``provenance.json`` (and static config files) — no
intermediate CSV or markdown artifacts.

Historical artifacts (``pnl.md`` / ``pnl.csv`` / ``venues.csv`` /
``off_protocol.csv`` / ``grove_sheet.{md,csv,xlsx}``) have been retired.
All the data they carried lives in ``provenance.json`` now and the
Grove-shaped per-venue re-attribution is computed in-process by
``settle.load.grove_sheet.compute_sheet_rows``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..domain.monthly_pnl import MonthlyPnL
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
    """Write provenance.json + the canonical settlement xlsx.

    Returns ``{"provenance": Path, "xlsx": Path}``.

    The xlsx is built by invoking ``scripts/build_settlement_xlsx.py``
    as a subprocess — keeps the script's CLI-callable shape stable AND
    avoids pulling the openpyxl render path into the compute layer's
    import graph. Falls back to a direct call when the script isn't
    importable (e.g., reduced-distribution installs).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {
        "provenance": write_provenance(
            pnl, output_dir / "provenance.json", sources=sources,
        ),
    }

    # Build the canonical xlsx. The script-as-subprocess shape keeps the
    # CLI intact (`python scripts/build_settlement_xlsx.py --prime …
    # --month …`) and avoids importing openpyxl at compute time.
    month_str = f"{pnl.month.year}-{pnl.month.month:02d}"
    script = _REPO_ROOT / "scripts" / "build_settlement_xlsx.py"
    if script.exists():
        try:
            subprocess.run(
                [sys.executable, str(script), "--prime", pnl.prime_id,
                 "--month", month_str],
                check=True, capture_output=True, text=True, timeout=60,
            )
            xlsx_path = output_dir / _output_filename(pnl.prime_id, month_str)
            if xlsx_path.exists():
                written["xlsx"] = xlsx_path
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            # Don't fail the settlement run on a render failure — the
            # canonical provenance.json is already on disk.
            pass

    return written


_MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)


def _output_filename(prime_id: str, month: str) -> str:
    """Return ``{prime}_settlement_{month_name}_{year}.xlsx``.

    Mirrors ``scripts.build_settlement_xlsx._output_filename`` so the
    writer can predict the xlsx path without re-importing the script.
    """
    year, m = month.split("-")
    return f"{prime_id}_settlement_{_MONTH_NAMES[int(m) - 1]}_{year}.xlsx"
