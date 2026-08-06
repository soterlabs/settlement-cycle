"""Chronicle oracle reader.

Chronicle is MakerDAO/Sky's native oracle service. The Scribe-family contracts
expose ``read()`` (returns ``uint256`` scaled to 1e18) — and revert if the
caller is not on the feed's kiss/allowlist. Reverts are surfaced as
``ChronicleReadError`` so the price-dispatch layer can fall through to a
configured fallback (Redstone, Chainlink) or raise.

Selectors:
- ``read()`` → 0x57de26a4
- ``readWithAge()`` → 0x393e5ede

Staleness guard (2026-08-06): Chronicle rotates VAO *Consumer* instances
(ACRDX went through 7 by Aug 2026); an abandoned consumer keeps returning
its last value forever, silently freezing NAV — the fallback chain never
fires because the read still succeeds. Grove E22 booked $0 revenue for
Jun+Jul 2026 this way (Consumer_2 froze 2026-05-07). ``read`` therefore
prefers ``readWithAge()`` and logs a WARNING when the price is older than
``STALE_WARN_SECONDS`` at the queried block. Config should point at the
per-asset *Router* (stable address, forwards to the live consumer), which
makes the warning a tripwire rather than a recurring event.

Layering note: only the RPC fetches (``_read_with_age_raw`` /
``_plain_read``) sit below ``@cached``. The staleness check runs in the
UNCACHED ``read()`` wrapper on every call — including cache-hit re-runs —
so a settlement regenerated months later still logs the frozen-feed
warning (``block_timestamp`` is itself cached, so the re-check is free).

A zero value from either path raises ``ChronicleReadError``: Chronicle
feeds signal "no published price" by reverting, so a well-formed zero
(e.g. a Router forwarding to an unset consumer) is a no-value state, and
raising lets the dispatch layer price the venue off its configured
fallback instead of marking the position at $0.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from ...domain.primes import Address, Chain
from .._abi import decode_uint_words
from ..cache import cached
from ..rpc import RPCError, block_timestamp, eth_call

_log = logging.getLogger(__name__)

SEL_READ = "0x57de26a4"
SEL_READ_WITH_AGE = "0x393e5ede"

# Warn when the feed's last update is older than this at the queried block.
# VAO NAV feeds for the assets we track (STAC, JAAA, JTRSY, ACRDX) update
# at least weekly; 14 days of silence means a rotated-out or broken feed.
STALE_WARN_SECONDS = 14 * 24 * 3600

_WAD = Decimal(10**18)


class ChronicleReadError(RuntimeError):
    """Raised when the feed has no usable value at the queried block —
    a revert (caller not on the kiss/allowlist, no published price) or a
    well-formed zero return. The price-dispatch layer falls back."""


def _to_usd(value_raw: int) -> Decimal:
    return Decimal(value_raw) / _WAD


@cached(source_id="chronicle.read_with_age")
def _read_with_age_raw(
    chain: Chain, oracle: Address, block: int,
) -> tuple[int, int] | None:
    """``(value_raw, age)`` from ``readWithAge()``, or ``None`` when the
    contract doesn't support the selector (revert / empty / short return).
    Only the RPC result is cached — interpretation happens in ``read``."""
    try:
        result = eth_call(chain, oracle, SEL_READ_WITH_AGE, block)
    except RPCError:
        return None
    try:
        value_raw, age = decode_uint_words(result, 2)
    except ValueError:
        return None
    return value_raw, age


@cached(source_id="chronicle.read")
def _plain_read(chain: Chain, oracle: Address, block: int) -> Decimal:
    """Legacy single-value ``read()`` path, for contracts without
    ``readWithAge``. Same cache ``source_id`` as the original ``read`` so
    previously cached values stay valid."""
    try:
        result = eth_call(chain, oracle, SEL_READ, block)
    except RPCError as e:
        raise ChronicleReadError(
            f"Chronicle read() reverted at {oracle.hex} on {chain.value} "
            f"block {block}: {e}"
        ) from e

    value_raw = int(result, 16)
    return _to_usd(value_raw)


def read(chain: Chain, oracle: Address, block: int) -> Decimal:
    """Read the Chronicle price at ``block``. Returns USD-denominated `Decimal`.

    Prefers ``readWithAge()`` so a frozen feed (rotated-out VAO consumer)
    surfaces as a WARNING; falls back to plain ``read()`` for contracts
    without ``readWithAge``. Staleness never raises — the log line is the
    tripwire — but a ZERO value does (no published price → fallback).

    Deliberately uncached: the underlying RPC fetches are cached, so this
    wrapper re-evaluates the staleness warning on every call, including
    settlement re-runs served entirely from cache.
    """
    pair = _read_with_age_raw(chain, oracle, block)
    if pair is None:
        return _plain_read(chain, oracle, block)

    value_raw, age = pair
    if value_raw == 0:
        raise ChronicleReadError(
            f"Chronicle readWithAge() returned zero value at {oracle.hex} on "
            f"{chain.value} block {block} — no published price (unset "
            "consumer?); falling back via dispatch."
        )
    _warn_if_stale(chain, oracle, block, value_raw, age)
    return _to_usd(value_raw)


def _warn_if_stale(
    chain: Chain, oracle: Address, block: int, value_raw: int, age: int,
) -> None:
    """Log a WARNING when the feed's last update is older than
    ``STALE_WARN_SECONDS`` at ``block``. A failed ``block_timestamp``
    lookup is logged and skipped — it must never discard the
    already-fetched price or force a second RPC read."""
    try:
        staleness = block_timestamp(chain, block) - age
    except RPCError as e:
        _log.warning(
            "Chronicle staleness check SKIPPED for %s on %s at block %d — "
            "block_timestamp failed (%s). Price accepted unverified.",
            oracle.hex, chain.value, block, e,
        )
        return
    if staleness > STALE_WARN_SECONDS:
        _log.warning(
            "Chronicle feed %s on %s is STALE at block %d: last update "
            "%.1f days before the queried block (value %s). Likely a "
            "rotated-out VAO consumer — point config at the asset's "
            "Router contract instead (see chronicle.py module docs).",
            oracle.hex, chain.value, block, staleness / 86400,
            _to_usd(value_raw),
        )
