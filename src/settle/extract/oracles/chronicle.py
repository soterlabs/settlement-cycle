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
"""

from __future__ import annotations

import logging
from decimal import Decimal

from ...domain.primes import Address, Chain
from ..cache import cached
from ..rpc import RPCError, block_timestamp, eth_call

_log = logging.getLogger(__name__)

SEL_READ = "0x57de26a4"
SEL_READ_WITH_AGE = "0x393e5ede"

# Warn when the feed's last update is older than this at the queried block.
# VAO NAV feeds for the assets we track (STAC, JAAA, JTRSY, ACRDX) update
# at least weekly; 14 days of silence means a rotated-out or broken feed.
STALE_WARN_SECONDS = 14 * 24 * 3600


class ChronicleReadError(RuntimeError):
    """Raised when ``read()`` reverts. Most commonly due to the caller address
    not being on the feed's kiss/allowlist."""


@cached(source_id="chronicle.read")
def read(chain: Chain, oracle: Address, block: int) -> Decimal:
    """Read the Chronicle price at ``block``. Returns USD-denominated `Decimal`.

    Prefers ``readWithAge()`` so a frozen feed (rotated-out VAO consumer)
    surfaces as a WARNING instead of silently pinning NAV; falls back to
    plain ``read()`` for contracts without ``readWithAge``. Staleness never
    raises — value semantics are unchanged — the log line is the tripwire.

    Cached at the Extract layer; same (chain, oracle, block) triple returns
    the cached value on subsequent calls.
    """
    try:
        result = eth_call(chain, oracle, SEL_READ_WITH_AGE, block)
        if len(result) < 130:
            # Not a 2-word (value, age) return — contract predates
            # readWithAge or returned empty. Use the plain read() path.
            raise ValueError("readWithAge returned <2 words")
        value_raw = int(result[2:66], 16)
        age = int(result[66:130], 16)
        staleness = block_timestamp(chain, block) - age
        if staleness > STALE_WARN_SECONDS:
            _log.warning(
                "Chronicle feed %s on %s is STALE at block %d: last update "
                "%.1f days before the queried block (value %.6f). Likely a "
                "rotated-out VAO consumer — point config at the asset's "
                "Router contract instead (see chronicle.py module docs).",
                oracle.hex, chain.value, block, staleness / 86400,
                value_raw / 1e18,
            )
        return Decimal(value_raw) / Decimal(10**18)
    except (RPCError, ValueError, IndexError):
        # No readWithAge() on this contract (older Scribe) or malformed
        # return — fall through to the plain read() path.
        pass
    try:
        result = eth_call(chain, oracle, SEL_READ, block)
    except RPCError as e:
        raise ChronicleReadError(
            f"Chronicle read() reverted at {oracle.hex} on {chain.value} "
            f"block {block}: {e}"
        ) from e

    value_raw = int(result, 16)
    return Decimal(value_raw) / Decimal(10**18)
