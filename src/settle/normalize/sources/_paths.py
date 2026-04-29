"""Resolve the on-disk path to the bundled ``queries/`` directory at runtime.

SQL queries are shipped as package data under ``settle/queries/`` so they
resolve correctly whether the project is installed editable (``pip install -e``)
or as a built wheel (where ``parents[N]``-style paths fall outside the package).
"""

from __future__ import annotations

from pathlib import Path

# Sibling of `settle/normalize/`, inside the `settle` package itself.
QUERIES_DIR = Path(__file__).resolve().parents[2] / "queries"
