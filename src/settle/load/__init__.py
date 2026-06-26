"""Load — render ``MonthlyPnL`` to the canonical settlement artifacts.

Two outputs per monthly run:
  * ``provenance.json`` — machine-readable canonical (written by
    ``write_provenance``).
  * ``{prime}_settlement_{month_name}_{year}.xlsx`` — human-readable
    multi-tab Excel (rendered by ``scripts.build_settlement_xlsx`` from
    ``provenance.json``, invoked as a subprocess by ``write_settlement``).

The Grove-PnL-workbook-shaped per-venue re-attribution
(``profit_to_sky`` / ``profit_to_grove`` / ``cof_alloc``) is computed
in-process by ``settle.load.grove_sheet.compute_sheet_rows`` — used by
the xlsx renderer and exposed for ad-hoc analysis.

Historical artifacts (``pnl.md`` / ``pnl.csv`` / ``venues.csv`` /
``off_protocol.csv`` / ``grove_sheet.{md,csv,xlsx}``) have been retired.
All data is in ``provenance.json``; the Grove-sheet math is derivable
from it deterministically.
"""

from .grove_sheet import compute_sheet_rows
from .provenance import write_provenance
from .summary import render_summary, write_summary
from .writer import default_output_dir, refresh_dr_only, write_settlement

__all__ = [
    "compute_sheet_rows",
    "default_output_dir",
    "refresh_dr_only",
    "render_summary",
    "write_provenance",
    "write_settlement",
    "write_summary",
]
