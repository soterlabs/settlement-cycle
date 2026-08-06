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


def decode_uint_words(result: str, n_words: int) -> list[int]:
    """Decode the first ``n_words`` 32-byte words of an ``eth_call`` return
    as unsigned ints. Raises ``ValueError`` when the return is shorter than
    ``n_words`` words or not valid hex — callers treat that as "selector not
    supported / empty return" and fall through to their legacy path."""
    h = result.removeprefix("0x")
    if len(h) < 64 * n_words:
        raise ValueError(
            f"expected ≥{n_words} ABI words ({64 * n_words} hex chars), got {len(h)}"
        )
    return [int(h[i * 64:(i + 1) * 64], 16) for i in range(n_words)]
