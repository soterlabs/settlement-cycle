"""Lightweight DataFrame validation. Use Pandera if it gets installed; for now,
just enforce column presence at the Normalize → Compute boundary.
"""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


class SchemaError(ValueError):
    """Raised when a Normalize-layer DataFrame doesn't have the expected shape."""


def assert_columns(df: pd.DataFrame, expected: Iterable[str]) -> None:
    """Raise `SchemaError` if any of `expected` columns are missing.

    Empty DataFrames are allowed (no rows) but must still have the columns.
    For an `empty no-columns` frame, allow it too — Source returned no data.
    """
    if df.empty and len(df.columns) == 0:
        return
    missing = [c for c in expected if c not in df.columns]
    if missing:
        raise SchemaError(
            f"DataFrame missing required columns {missing}; "
            f"got {list(df.columns)}"
        )
