"""Read the consolidated Sky Net Revenue from a ``settlements/sky_total/<month>``
artifact — the one loader shared by every consumer of SNR (GAR, TMF).

Order of preference:

1. ``provenance.json`` → ``results.sky_net_revenue`` (full precision, plus the
   ``generated_at_utc`` stamp for audit strings);
2. the committed ``summary.md`` — provenance is gitignored, so a fresh clone
   only has the markdown. The headline row is parsed; ``generated`` then
   names the file rather than a timestamp.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

__all__ = ["SnrArtifact", "read_sky_total_snr"]

_SUMMARY_ROW = re.compile(r"\*\*Sky Net Revenue\*\*\s*\|\s*\*\*(-?[\d,]+\.?\d*)\*\*")


@dataclass(frozen=True)
class SnrArtifact:
    snr: Decimal
    generated: str   # provenance ``generated_at_utc`` (or its id), else "summary.md"
    path: Path


def read_sky_total_snr(repo_root: Path, label: str) -> SnrArtifact | None:
    """SNR for month ``label`` (``YYYY-MM``), or None when no artifact exists."""
    d = repo_root / "settlements" / "sky_total" / label
    prov = d / "provenance.json"
    if prov.exists():
        data = json.loads(prov.read_text())
        return SnrArtifact(
            snr=Decimal(str(data["results"]["sky_net_revenue"])),
            generated=str(data.get("generated_at_utc") or data.get("id", "sky_total")),
            path=prov,
        )
    summ = d / "summary.md"
    if summ.exists():
        m = _SUMMARY_ROW.search(summ.read_text())
        if m:
            return SnrArtifact(
                snr=Decimal(m.group(1).replace(",", "")), generated="summary.md", path=summ,
            )
    return None
