"""Minimal pure-Python keccak-256 (Ethereum variant).

Vendored to avoid a new third-party dependency: the ``extract.*`` layer is
intentionally web3-free (see ``extract/rpc.py``), and Python's stdlib
``hashlib.sha3_256`` is NIST SHA3 (different padding) — not the keccak-256
Ethereum uses. Uniswap v4 needs keccak-256 to derive ``poolId`` from a
``PoolKey`` and to compute the ``PoolManager`` storage slot for a pool's
state. This is the only place the engine needs a hash primitive.

Reference: Keccak[r=1088, c=512] with 0x01 domain padding, 256-bit output.
"""

from __future__ import annotations

_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
_ROT = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)
_MASK = (1 << 64) - 1


def _rol(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & _MASK


def _keccak_f(state: list[list[int]]) -> None:
    for rnd in range(24):
        # θ
        c = [state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x][y] ^= d[x]
        # ρ and π
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(state[x][y], _ROT[x][y])
        # χ
        for x in range(5):
            for y in range(5):
                state[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
        # ι
        state[0][0] ^= _RC[rnd]


def keccak256(data: bytes) -> bytes:
    """Return the 32-byte keccak-256 digest of ``data``."""
    rate = 136  # bytes (1088 bits)
    state = [[0] * 5 for _ in range(5)]

    # Absorb with 0x01 domain padding + 0x80 final bit (pad10*1 over the rate).
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate != 0:
        padded.append(0x00)
    padded[-1] ^= 0x80

    for off in range(0, len(padded), rate):
        block = padded[off:off + rate]
        for i in range(rate // 8):
            lane = int.from_bytes(block[i * 8:i * 8 + 8], "little")
            state[i % 5][i // 5] ^= lane
        _keccak_f(state)

    # Squeeze (one block is enough for 256-bit output).
    out = bytearray()
    for i in range(rate // 8):
        out += int(state[i % 5][i // 5]).to_bytes(8, "little")
        if len(out) >= 32:
            break
    return bytes(out[:32])
