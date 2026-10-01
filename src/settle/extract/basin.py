"""Strict historical reads for Basin idle-USDS deductions.

GroveBasin uses internal shares, not ERC-20 LP balances. Its active pocket
can change (PocketSet); resolve pocket() at every pinned historical block.
Contract: https://github.com/grove-labs/grove-basin/blob/main/src/GroveBasin.sol
"""
from decimal import Decimal

from ..domain.primes import Address, Chain
from ._keccak import keccak256
from .cache import cached
from .rpc import RPCError, eth_call

USDS = Address.from_str("0xdc035d45d973e3ec169d2276ddab16f1e407384f")
VAT = Address.from_str("0x35d1b3f3d7966a1dfe207aa4514c12a259a0492b")


def _word(contract: Address, signature: str, block: int, argument: bytes = b"") -> int:
    selector = keccak256(signature.encode())[:4].hex()
    raw = eth_call(Chain.ETHEREUM, contract, "0x" + selector + argument.hex(), block)
    if len(raw) != 66 or not raw.startswith("0x"):
        raise RPCError(f"Invalid Basin response for {signature}")
    return int(raw[2:], 16)


def _address(contract: Address, signature: str, block: int) -> Address:
    raw = _word(contract, signature, block)
    if not 0 < raw < 2**160:
        raise RPCError(f"Invalid Basin address for {signature}")
    return Address(raw.to_bytes(20, "big"))


@cached(source_id="basin.idle_usds")
def idle_usds(
    basin: Address, holder: Address, block: int, *, chain: Chain = Chain.ETHEREUM,
) -> dict:
    """Raw balances and ownership at one block; failures never become zero."""
    # The chain argument also subjects outer cache hits to the daily worker
    # finalized-block guard, before any nested RPC read or cache lookup.
    if chain != Chain.ETHEREUM:
        raise ValueError("Basin idle exemption is Ethereum-only")
    if _address(basin, "swapToken()", block) != USDS:
        raise ValueError("Basin idle exemption requires USDS as swapToken")
    pocket = _address(basin, "pocket()", block)
    shares = _word(basin, "shares(address)", block, holder.value.rjust(32, b"\0"))
    total = _word(basin, "totalShares()", block)
    if shares > total:
        raise ValueError("Basin holder shares exceed total shares")
    # Self-pocket Basins keep the cash on the Basin itself: count it once.
    balances = {a.hex: _word(USDS, "balanceOf(address)", block, a.value.rjust(32, b"\0"))
                for a in {basin, pocket}}
    return {"pocket": pocket.hex, "shares": shares, "total_shares": total,
            "balances": balances}


@cached(source_id="basin.ilk_debt")
def ilk_debt(ilk: bytes, block: int, *, chain: Chain = Chain.ETHEREUM) -> Decimal:
    """Actual USDS owed by this ilk, including its own Vat rate (Art x rate)."""
    if chain != Chain.ETHEREUM:
        raise ValueError("Basin debt cap is Ethereum-only")
    raw = eth_call(chain, VAT, "0xd9638d36" + ilk.hex(), block)
    if len(raw) != 322 or not raw.startswith("0x"):
        raise RPCError("Invalid Vat.ilks response for Basin debt cap")
    art, rate = int(raw[2:66], 16), int(raw[66:130], 16)
    if art and not rate:
        raise ValueError("Nonzero Basin ilk Art with zero rate")
    return Decimal(art * rate) / Decimal(10**45)
