"""Top-level settlement writer + default output-path resolver.

Emits three artifacts per monthly run:
  - ``provenance.json``                                 (machine-readable,
                                                         large, regenerable,
                                                         **gitignored**)
  - ``summary.md``                                      (text-only, small,
                                                         deterministic —
                                                         the **PR-review
                                                         surface**, tracked
                                                         in git)
  - ``{prime}_settlement_{month_name}_{year}.xlsx``     (canonical
                                                         human-readable
                                                         multi-tab Excel,
                                                         tracked in git)

The xlsx is rendered by ``scripts.build_settlement_xlsx`` (subprocess —
reads only ``provenance.json``). The summary is rendered in-process by
``settle.load.summary.write_summary``. The Grove-shaped per-venue
re-attribution used by the xlsx is computed in-process by
``settle.load.grove_sheet.compute_sheet_rows``.

Historical artifacts (``pnl.md`` / ``pnl.csv`` / ``venues.csv`` /
``off_protocol.csv`` / ``grove_sheet.{md,csv,xlsx}``) have been retired.
"""

from __future__ import annotations

import os
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from ..domain.monthly_pnl import MonthlyPnL
from .dr_rewards import enrich_with_dr, load_dr
from .provenance import write_provenance
from .summary import write_summary

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

    # Populate distribution rewards (+ per-ref-code breakdown) from the
    # settle-dr-dune reconciliation workbook. No-op when the submodule is
    # absent or the prime has no tagged DR — see ``dr_rewards.enrich_with_dr``.
    pnl = enrich_with_dr(pnl)

    prov_path = write_provenance(
        pnl, output_dir / "provenance.json", sources=sources,
    )
    written: dict[str, Path] = {
        "provenance": prov_path,
        "summary":    write_summary(prov_path, output_dir / "summary.md"),
    }

    # Build the canonical xlsx (subprocess — keeps the CLI shape, avoids
    # importing openpyxl at compute time).
    month_str = f"{pnl.month.year}-{pnl.month.month:02d}"
    xlsx_path = _build_canonical_xlsx(pnl.prime_id, month_str, output_dir)
    if xlsx_path is not None:
        written["xlsx"] = xlsx_path

    return written


def _build_canonical_xlsx(prime_id: str, month_str: str, output_dir: Path) -> Path | None:
    """Render the canonical xlsx from ``output_dir/provenance.json`` via the
    ``build_settlement_xlsx`` script. Returns the path, or ``None`` on a
    render failure / missing script (never raises — the provenance.json is
    already the canonical record)."""
    script = _REPO_ROOT / "scripts" / "build_settlement_xlsx.py"
    if not script.exists():
        return None
    try:
        subprocess.run(
            [sys.executable, str(script), "--prime", prime_id, "--month", month_str],
            check=True, capture_output=True, text=True, timeout=60,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    xlsx_path = output_dir / _output_filename(prime_id, month_str)
    return xlsx_path if xlsx_path.exists() else None


def refresh_dr_only(prime_id: str) -> list[Path]:
    """Refresh ONLY Distribution Rewards in already-written settlements.

    For each existing ``settlements/<prime_id>/<month>/provenance.json``, patch
    the DR fields from the settle-dr-dune workbook and re-render summary.md +
    xlsx. **No recompute** — no RPC / Dune calls. Requires a prior full run
    (the provenance.json must exist). The patch is idempotent: it applies the
    delta vs the current ``distribution_rewards`` so re-running is safe and
    submodule updates flow through.
    """
    base = _REPO_ROOT / "settlements" / prime_id
    if not base.exists():
        print(f"no settlements/{prime_id}/ — run a full settlement first")
        return []
    updated: list[Path] = []
    for prov_path in sorted(base.glob("*/provenance.json")):
        with prov_path.open() as f:
            prov = json.load(f)
        month = prov.get("month") or f"{prov['period']['start'][:7]}"
        dr = load_dr(prime_id, month)
        if dr is None:
            # No DR source for this prime/month (unmapped prime, missing
            # submodule, or out-of-range month) — leave the report untouched
            # rather than destructively zeroing a previously-published value.
            print(f"  {month}: no DR data — left unchanged")
            continue
        results = prov["results"]
        new = dr["total"]
        par = Decimal(str(results.get("prime_agent_revenue", "0")))
        ar = Decimal(str(results.get("agent_rate", "0")))
        sky = Decimal(str(results.get("sky_revenue", "0")))
        # Demand-side components that live alongside DR in the totals —
        # dropping them here silently strips them from a previously
        # published provenance (Grove: chronicle_points; Skybase: gar).
        cp = Decimal(str(results.get("chronicle_points", "0")))
        gar = Decimal(str(results.get("gar", "0")))
        # Recompute the dependent totals from components (not a delta patch),
        # so an already-stale provenance is corrected and the result is
        # idempotent. Mirrors MonthlyPnL.prime_agent_total_revenue and the
        # __post_init__ monthly_pnl invariant.
        results["distribution_rewards"] = str(new)
        results["prime_agent_total_revenue"] = str(par + ar + new + cp + gar)
        results["monthly_pnl"] = str(par + ar + new + cp + gar - sky)
        prov["dr_breakdown"] = [
            {"ref_code": r["ref_code"], "amount": str(r["amount"]), "notes": r["notes"]}
            for r in dr["rows"]
        ]
        with prov_path.open("w") as f:
            json.dump(prov, f, indent=2)
        out_dir = prov_path.parent
        write_summary(prov_path, out_dir / "summary.md")
        _build_canonical_xlsx(prime_id, month, out_dir)
        print(f"  {month}: distribution_rewards={float(new):,.2f} ({len(dr['rows'])} ref codes)")
        updated.append(out_dir)
    return updated


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
