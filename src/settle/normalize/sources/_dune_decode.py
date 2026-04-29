"""Shared decoders for Dune query results.

Each Dune source historically redefined ``_to_decimal`` and the
``pd.to_datetime(...).dt.date`` + sort/reset boilerplate. Consolidating here
keeps the "Dune numerics → Decimal via str" policy in one place; if Dune
ever returns a type that needs different handling, only one site changes.
"""

from __future__ import annotations

from decimal import Decimal


def to_decimal(v: object) -> Decimal:
    """Coerce a Dune-returned numeric to ``Decimal`` via ``str(v)``.

    Going through ``str`` avoids the ``Decimal(float)`` precision artifacts
    (e.g. ``Decimal(0.1) == Decimal('0.1000000000000000055511151231257827021181583404541015625')``).
    """
    return Decimal(str(v))


def to_addr_bytes(v: object) -> bytes:
    """Coerce a Dune varbinary to a fixed 20-byte address.

    Dune may return varbinary as ``bytes``, ``bytearray``, ``memoryview``, or
    a ``"0x"``-prefixed hex string; leading zero bytes are sometimes stripped,
    so the input may be shorter than 20 bytes. Normalize to exactly 20 bytes
    so downstream membership against ``Address.value`` works reliably.
    """
    if isinstance(v, str):
        b = bytes.fromhex(v.removeprefix("0x"))
    elif isinstance(v, memoryview):
        b = bytes(v)
    elif isinstance(v, (bytes, bytearray)):
        b = bytes(v)
    else:
        raise TypeError(f"unexpected counterparty type: {type(v).__name__}")
    if len(b) > 20:
        raise ValueError(f"address longer than 20 bytes: {b.hex()}")
    return b.rjust(20, b"\x00")
