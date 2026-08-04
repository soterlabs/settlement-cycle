"""Shared ``--months`` CLI filter for the per-prime settlement runners.

Replaces seven copy-pasted ``_selected_months`` / ``_selected_plan`` helpers
that shared two silent failure modes (found in the 2026-08 PR-164 review):

* ``--months`` as the last argv token raised an unhandled ``IndexError``
  instead of a usage message;
* a value matching NOTHING (typo, month not in the runner's plan) selected
  zero months and the runner still exited 0 with an empty "Artifacts
  written:" section — CI and operators read that as a successful settlement.

Both are now loud ``SystemExit`` errors.
"""

from __future__ import annotations

import sys
from typing import Callable, Sequence, TypeVar

T = TypeVar("T")


def filter_by_months(
    items: Sequence[T],
    ym_of: Callable[[T], tuple[int, int]],
    *,
    argv: list[str] | None = None,
) -> list[T]:
    """Apply an optional ``--months YYYY-MM[,YYYY-MM…]`` argv filter.

    ``ym_of`` maps each item to its ``(year, month)`` tuple — items are
    ``Month`` objects for the flat runners and ``(year, month, fixture_dir)``
    plan entries for Spark/Grove. No ``--months`` flag → all items.
    """
    argv = sys.argv if argv is None else argv
    if "--months" not in argv:
        return list(items)
    i = argv.index("--months")
    if i + 1 >= len(argv):
        raise SystemExit(
            "--months requires a value, e.g. --months 2026-07 or "
            "--months 2026-06,2026-07"
        )
    raw = argv[i + 1]
    try:
        want = set()
        for part in raw.split(","):
            y, m = part.split("-")
            want.add((int(y), int(m)))
    except ValueError:
        raise SystemExit(
            f"--months: could not parse {raw!r} — expected "
            "YYYY-MM[,YYYY-MM…], e.g. --months 2026-07"
        ) from None
    out = [it for it in items if ym_of(it) in want]
    if not out:
        available = ", ".join(f"{y}-{m:02d}" for y, m in sorted(ym_of(it) for it in items))
        raise SystemExit(
            f"--months {raw}: no matching months in this runner's plan "
            f"(available: {available}). Nothing was run."
        )
    return out
