"""Governance Accessibility Rewards (GAR) — Skybase's Demand-Side primitive.

``GAR(month N) = GarConfig.share (1%) × Sky Net Revenue of month N``,
with SNR read from ``settlements/sky_total/<N>/provenance.json`` (whose
definition matches BA's "Net revenue" dashboard line).

Timing (operator, 2026-08-06): the month-N report carries month N's GAR;
the cash is PAID at the MSC settling cycle N, which executes in month N+1
(July's GAR rides MSC#11 in August). There is no circularity: SNR(N) is
paid-basis — it carries the settlement executed IN month N (cycle N−1's
payments), so it never contains month N's own GAR; the GAR(N) payment
reduces SNR(N+1) through the normal subproxy-send subtraction.

Ordering: ``scripts/build_sky_total_2026.py`` must run for month N before
the GAR prime's month-N report — a missing artifact fails loud.
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
      * ``"<share> × SNR <month> = <snr> (sky_total generated <ts>)"`` —
        computed. A negative SNR is floored to $0 (a rewards primitive
        doesn't claw back) with the floor noted in the basis.
    """
    if prime.gar is None or str(month) < prime.gar.from_month:
        return Decimal("0"), ""

    label = str(month)
    root = repo_root if repo_root is not None else REPO_ROOT
    prov_path = root / "settlements" / "sky_total" / label / "provenance.json"

    if not prov_path.exists():
        raise FileNotFoundError(
            f"gar: {prime.id} {label} needs settlements/sky_total/"
            f"{label}/provenance.json (GAR = share × the month's SNR) — "
            "run scripts/build_sky_total_2026.py first.\n"
            "NOTE (accrual months): sky_total in turn reads this prime's "
            "report, so the two bootstrap in a cycle. Break it the way the "
            f"July cycle was: generate {prime.id} once (the run fails here), "
            "pin the intended GAR for the month under "
            f"config/sky_total.yaml → msc_preview['{label}']['{prime.id}']"
            ".gar_in_dv, build sky_total, then re-run this report — its GAR "
            "becomes share × the now-frozen SNR."
        )

    prov = json.loads(prov_path.read_text())
    snr = Decimal(prov["results"]["sky_net_revenue"])
    generated = prov.get("generated_at_utc") or prov.get("id", "sky_total")
    gar = prime.gar.share * snr
    if gar < 0:
        _log.warning(
            "gar: %s %s SNR is negative (%.2f) — floored to $0 "
            "(rewards don't claw back)", prime.id, month, float(snr),
        )
        return Decimal("0"), (
            f"{prime.gar.share} × SNR {label} = {snr} — NEGATIVE, "
            f"floored to 0 (sky_total generated {generated})"
        )
    basis = (
        f"{prime.gar.share} × SNR {label} = {snr} "
        f"(sky_total generated {generated})"
    )
    _log.info(
        "gar: %s earns $%.2f (= %s × SNR %s $%.2f); paid at the MSC "
        "executing the following month",
        prime.id, float(gar), prime.gar.share, label, float(snr),
    )
    return gar, basis
