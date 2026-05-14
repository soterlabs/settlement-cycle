"""Scan ERC-20 ``Transfer(address indexed from, address indexed to,
uint256 value)`` events on a single chain + token via ``eth_getLogs``.

Underpins :class:`~settle.normalize.sources.rpc_balances.RPCBalanceSource`,
which provides ``IBalanceSource`` on chains not in Dune's spellbook
(currently Monad / Unichain / Plume).

Cached by ``(chain, token, from_block, to_block, [from_filter],
[to_filter])`` so repeated calls inside the same settlement period are
free. Returns a list of plain tuples so the JSON-envelope path in
``postgres_store.encode_payload`` round-trips cleanly without pickling
custom dataclasses.
"""

from __future__ import annotations

from ..domain.primes import Address, Chain
from .cache import cached
from .rpc import eth_get_logs

# keccak256("Transfer(address,address,uint256)")
TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _to_topic_hex(addr: bytes) -> str:
    """Encode a 20-byte address as a 32-byte left-padded ``0x``-prefixed
    topic string suitable for ``eth_getLogs`` indexed-topic filters."""
    if len(addr) != 20:
        raise ValueError(f"Address must be 20 bytes; got {len(addr)}")
    return "0x" + ("00" * 12) + addr.hex()


def _addr_from_topic(topic: str) -> bytes:
    """Extract the 20-byte address from a 32-byte ``0x``-prefixed topic
    (the last 20 bytes; first 12 are left-padding zeros)."""
    raw = bytes.fromhex(topic.removeprefix("0x"))
    if len(raw) != 32:
        raise ValueError(f"Topic not 32 bytes: {topic}")
    return raw[12:]


@cached(source_id="rpc.scan_transfers")
def scan_transfers(
    chain: str,
    token: bytes,
    from_block: int,
    to_block: int,
    *,
    from_filter: bytes | None = None,
    to_filter: bytes | None = None,
) -> list[tuple[int, bytes, bytes, int]]:
    """Return ``Transfer`` events for ``token`` in ``[from_block, to_block]``,
    optionally filtered by indexed ``from`` and/or ``to`` address.

    Returns a list of tuples ``(block_number, from_addr, to_addr, value_raw)``.
    ``value_raw`` is the on-chain uint256 (not decimal-adjusted); the caller
    divides by ``10**decimals`` to get human-readable amounts.

    For a single chain × token, one scan with ``from_filter`` AND
    ``to_filter`` set both pins down the holder-to-counterparty edge in a
    single RPC pass; a holder-only query (``cumulative_balance`` style)
    needs two scans (one with ``from_filter``, one with ``to_filter``)
    because ``eth_getLogs`` only supports AND on topics.
    """
    topics: list[str | None] = [TRANSFER_TOPIC0]
    # Topic positions: 0 = event signature, 1 = from, 2 = to.
    # ``eth_get_logs`` accepts None at any position to leave that filter open.
    if from_filter is not None or to_filter is not None:
        topics.append(_to_topic_hex(from_filter) if from_filter is not None else None)
        if to_filter is not None:
            topics.append(_to_topic_hex(to_filter))
    raw_logs = eth_get_logs(Chain(chain), Address(token), topics, from_block, to_block)
    out: list[tuple[int, bytes, bytes, int]] = []
    for log in raw_logs:
        # eth_getLogs sometimes returns ``data == "0x"`` for ERC-721 Transfer
        # events (which share topic0 with ERC-20 but encode the tokenId in
        # topic3 rather than ``data``). Skip — we're only interested in the
        # ERC-20 shape here.
        data = log.get("data", "0x")
        if data in ("0x", ""):
            continue
        out.append((
            int(log["blockNumber"], 16),
            _addr_from_topic(log["topics"][1]),
            _addr_from_topic(log["topics"][2]),
            int(data, 16),
        ))
    return out
