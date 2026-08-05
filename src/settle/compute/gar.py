"""Governance Accessibility Rewards (GAR) — Skybase's Demand-Side primitive.

``GAR(month N) = GarConfig.share (1%) × Sky Net Revenue of month N−1``,
with SNR read from ``settlements/sky_total/<N−1>/provenance.json`` (whose
definition matches BA's "Net revenue" dashboard line).

Why the PRIOR month: the base must be a final, already-published SNR at
report time, so there is no same-month circularity — month N's GAR is a
plain 1% of a known number. The cash itself rides Skybase's subproxy send
at the settlement of cycle N (executing in N+1) and reduces that later
month's SNR through the normal paid-basis subtraction, i.e. "1% of SNR of
month N is subtracted from a subsequent month's SNR", never its own.

Months whose BASE month predates the sky_total series (report month =
``GarConfig.from_month``, base = the month before it — concretely the
2026-01 report, whose base would be 2025-12) return an "N/A" basis: the
summary renders the row as N/A and books $0. A base month INSIDE the
series whose artifact is missing fails loud — run
``scripts/build_sky_total_2026.py`` first.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from pathlib import Path

from ..domain.period import Month
from ..domain.primes import Prime

__all__ = ["compute_gar", "REPO_ROOT"]

_log = logging.getLogger(__name__)

# Same resolution as settle.load.writer._REPO_ROOT (src/settle/<pkg>/x.py →
# repo). Kept here so compute doesn't import the load layer.
REPO_ROOT = Path(__file__).resolve().parents[3]


def _prev_month(month: Month) -> Month:
    if month.month == 1:
        return Month(month.year - 1, 12)
    return Month(month.year, month.month - 1)


def compute_gar(
    prime: Prime,
    month: Month,
    *,
    repo_root: Path | None = None,
) -> tuple[Decimal, str]:
    """Return ``(gar, basis)`` for ``prime`` in ``month``.

    ``basis`` is an audit string stored in provenance:
      * ``""`` — prime has no GAR program or the month predates it
        (no summary row);
      * ``"n/a: no sky_total artifact for <base>"`` — base month predates
        the sky_total series (row renders as N/A, $0 booked);
      * ``"<share> × SNR <base> = <snr> (sky_total generated <ts>)"`` —
        computed. A negative base SNR is floored to $0 (a rewards
        primitive doesn't claw back) with the floor noted in the basis.
    """
    if prime.gar is None or str(month) < prime.gar.from_month:
        return Decimal("0"), ""

    base = _prev_month(month)
    base_label = str(base)
    root = repo_root if repo_root is not None else REPO_ROOT
    prov_path = root / "settlements" / "sky_total" / base_label / "provenance.json"

    if base_label < prime.gar.from_month and not prov_path.exists():
        # The base month predates the tracked sky_total series (the
        # 2026-01 report's base is 2025-12) — N/A per the operator.
        return Decimal("0"), f"n/a: no sky_total artifact for {base_label}"

    if not prov_path.exists():
        raise FileNotFoundError(
            f"gar: {prime.id} {month} needs settlements/sky_total/"
            f"{base_label}/provenance.json (GAR = share × prior-month SNR) — "
            "run scripts/build_sky_total_2026.py first"
        )

    prov = json.loads(prov_path.read_text())
    snr = Decimal(prov["results"]["sky_net_revenue"])
    generated = prov.get("generated_at_utc") or prov.get("id", "sky_total")
    gar = prime.gar.share * snr
    if gar < 0:
        _log.warning(
            "gar: %s %s base SNR is negative (%.2f) — floored to $0 "
            "(rewards don't claw back)", prime.id, month, float(snr),
        )
        return Decimal("0"), (
            f"{prime.gar.share} × SNR {base_label} = {snr} — NEGATIVE, "
            f"floored to 0 (sky_total generated {generated})"
        )
    basis = (
        f"{prime.gar.share} × SNR {base_label} = {snr} "
        f"(sky_total generated {generated})"
    )
    _log.info(
        "gar: %s earns $%.2f (= %s × SNR %s $%.2f) for %s",
        prime.id, float(gar), prime.gar.share, base_label, float(snr), month,
    )
    return gar, basis
