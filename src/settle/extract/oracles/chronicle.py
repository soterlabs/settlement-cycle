"""Chronicle oracle reader.

Chronicle is MakerDAO/Sky's native oracle service. The Scribe-family contracts
expose ``read()`` (returns ``uint256`` scaled to 1e18) — and revert if the
caller is not on the feed's kiss/allowlist. Reverts are surfaced as
``ChronicleReadError`` so the price-dispatch layer can fall through to a
configured fallback (Redstone, Chainlink) or raise.

Selectors:
- ``read()`` → 0x57de26a4
"""

from __future__ import annotations

from decimal import Decimal

from ...domain.primes import Address, Chain
from ..cache import cached
from ..rpc import RPCError, eth_call

SEL_READ = "0x57de26a4"


class ChronicleReadError(RuntimeError):
    """Raised when ``read()`` reverts. Most commonly due to the caller address
    not being on the feed's kiss/allowlist."""


@cached(source_id="chronicle.read")
def read(chain: Chain, oracle: Address, block: int) -> Decimal:
    """Read the Chronicle price at ``block``. Returns USD-denominated `Decimal`.

    Cached at the Extract layer; same (chain, oracle, block) triple returns
    the cached value on subsequent calls.
    """
    try:
        result = eth_call(chain, oracle, SEL_READ, block)
    except RPCError as e:
        raise ChronicleReadError(
            f"Chronicle read() reverted at {oracle.hex} on {chain.value} "
            f"block {block}: {e}"
        ) from e

    value_raw = int(result, 16)
    return Decimal(value_raw) / Decimal(10**18)
