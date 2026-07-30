"""Shared HyperSync decoding helpers reused across sources
(``hypersync_non_msc``, ``hypersync_msc_buffer``, and any future raw-log
extractor).

Kept intentionally minimal: only the byte-for-byte-identical helpers migrated
here to avoid drift. Source-specific helpers (``_sint``,
``_note_calldata_word``, ``_ilk_topic``, ``_ilk_from_topic``) stay in their
owning module.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from ...extract._keccak import keccak256


def _sel(sig: str) -> str:
    """LogNote topic0 — 4-byte fn selector left-aligned in a 32-byte word."""
    return "0x" + keccak256(sig.encode()).hex()[:8] + "0" * 56


def _evt(sig: str) -> str:
    """Real (non-anonymous) event topic0 — full keccak of the signature."""
    return "0x" + keccak256(sig.encode()).hex()


def _addr_topic(addr: str) -> str:
    """Left-pad an Ethereum address into a 32-byte topic word (lower-cased)."""
    return "0x" + "0" * 24 + addr.lower().replace("0x", "")


def _word(data_hex: str, idx: int) -> int:
    """Return 32-byte word ``idx`` of a ``data`` blob as an unsigned int."""
    raw = data_hex[2:] if data_hex.startswith("0x") else data_hex
    return int(raw[idx * 64 : idx * 64 + 64] or "0", 16)


def _row(
    stream: str,
    label: str,
    amount: Any,
    event_date: date | None = None,
) -> dict[str, Any]:
    """Emit a single stream row.

    Sources that don't attribute a per-row date (msc_buffer's monthly
    aggregates, non_msc's cross-month totals) leave ``event_date`` at its
    default of ``None``. Sources that DO attribute a date (non_msc's PSM
    jar burns, surplus returns) pass it explicitly.

    The dict always carries all four keys; each source's ``pd.DataFrame(...,
    columns=[...])`` picks which columns to keep for its DataFrame contract.
    """
    return {"stream": stream, "label": label, "event_date": event_date, "amount": amount}
