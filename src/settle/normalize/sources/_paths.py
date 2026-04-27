"""Resolve the on-disk path to the ``queries/`` directory at runtime."""

from __future__ import annotations

from pathlib import Path

# Repo root is 4 levels above this file:
#   src/settle/normalize/sources/_paths.py  → settlement-cycle/
QUERIES_DIR = Path(__file__).resolve().parents[4] / "queries"
