"""``SDETable.overlaps_venue`` SoM-locked semantics must be LOUD, not silent.

The full-month-vs-day-level-pro-rating methodology is open question S24
(QUESTIONS.md); these tests pin the current behaviour (SoM-locked match)
and the warnings that make the two silent-drop cases visible:

* entry starts mid-period → skipped for the whole month;
* fixed entry ends mid-period → sd_share stays 1.0 past end_date.
"""

from __future__ import annotations

import logging
from datetime import date

from settle.domain.sde import SDEEntry, SDETable


def _entry(*, start: date, end: date | None = None, kind: str = "fixed") -> SDEEntry:
    return SDEEntry(
        prime_id="spark", venue_id="S24", chain="ethereum",
        kind=kind, cap_usd=None, pattern=None,
        start_date=start, end_date=end,
        label="test entry", source="",
    )


SOM = date(2025, 11, 1)
EOM = date(2025, 11, 30)


def test_mid_period_activation_is_skipped_with_warning(caplog):
    table = SDETable(entries=(_entry(start=date(2025, 11, 13)),))
    with caplog.at_level(logging.WARNING, logger="settle.domain.sde"):
        out = table.overlaps_venue("spark", "S24", SOM, EOM)
    assert out is None  # SoM-locked: current (S24-pending) semantics
    assert any(
        "SKIPPED for the whole month" in r.message and "S24" in r.message
        for r in caplog.records
    )


def test_som_active_entry_matches_without_warning(caplog):
    table = SDETable(entries=(_entry(start=date(2025, 10, 1)),))
    with caplog.at_level(logging.WARNING, logger="settle.domain.sde"):
        out = table.overlaps_venue("spark", "S24", SOM, EOM)
    assert out is not None
    assert not caplog.records


def test_no_entry_no_warning(caplog):
    table = SDETable(entries=(_entry(start=date(2026, 2, 1)),))  # past period
    with caplog.at_level(logging.WARNING, logger="settle.domain.sde"):
        out = table.overlaps_venue("spark", "S24", SOM, EOM)
    assert out is None
    assert not caplog.records


def test_fixed_entry_ending_mid_period_warns_but_still_matches(caplog):
    table = SDETable(
        entries=(_entry(start=date(2025, 10, 1), end=date(2025, 11, 12)),),
    )
    with caplog.at_level(logging.WARNING, logger="settle.domain.sde"):
        out = table.overlaps_venue("spark", "S24", SOM, EOM)
    assert out is not None  # current semantics: matched, sd_share = 1.0
    assert any(
        "ends mid-period" in r.message and "S24" in r.message
        for r in caplog.records
    )
