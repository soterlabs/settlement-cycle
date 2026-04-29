"""Shared ABI helpers for ``extract.*`` modules.

ERC-20-style padding + address decoding used by every on-chain reader. Each
extract module historically redefined these; consolidating here ensures any
encoding fix lands once.
"""

from __future__ import annotations

from ..domain.primes import Address


def pad_address(a: Address) -> str:
    """Left-pad a 20-byte address to a 32-byte ABI word (hex, no ``0x`` prefix)."""
    return a.value.hex().rjust(64, "0")


def pad_uint(n: int) -> str:
    """Left-pad an unsigned int to a 32-byte ABI word (hex, no ``0x`` prefix)."""
    if n < 0:
        raise ValueError("only unsigned ints supported")
    return hex(n)[2:].rjust(64, "0")


def decode_address(hex_word: str) -> Address:
    """Decode a 32-byte ABI word as an Ethereum address (last 20 bytes)."""
    h = hex_word.removeprefix("0x")
    if len(h) != 64:
        raise ValueError(f"expected 64-hex-char ABI word, got {len(h)}: {hex_word!r}")
    return Address(bytes.fromhex(h[-40:]))
