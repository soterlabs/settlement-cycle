"""Unit tests for the L2 sUSDS pricing path (Q-S25 / issue #75).

Covers:

1. ``rpc.psm3_convert_to_shares`` encodes the right ``eth_call`` calldata
   (selector ``0x3e5541f1`` for ``convertToShares(address,uint256)`` —
   NOT ``0xc6e6f592`` which is the 1-arg ERC-4626 form — followed by
   padded address + uint).
2. Both ``RPCPsm3Source.convert_to_shares`` and
   ``DunePsm3Source.convert_to_shares`` delegate to the cached RPC helper —
   point reads at a single block, no Dune preload required.

The full compute-path assertion (L2 sUSDS at ALM produces a non-zero 30 bps
Cat-B sub-case (a) spread instead of $0) is verified by the Q1 2026
regression run, not here — the orchestrator wiring under test is short and
the conditional is gated by ``value_som == 0 and venue.chain != ETHEREUM
and venue.chain in prime.psm``.
"""

from __future__ import annotations

from unittest.mock import patch

from settle.domain.primes import Address, Chain
from settle.extract import rpc
from settle.normalize.sources.dune_psm3 import DunePsm3Source
from settle.normalize.sources.rpc_position import RPCPsm3Source

# Spark Arbitrum PSM3 + sUSDS — concrete addresses to make the encoding
# assertion meaningful (a generic 0xdead… would also work but real values
# guard against accidental zero-padding bugs).
_PSM3_ARB  = Address.from_str("0x2B05F8e1cACC6974fD79A673a341Fe1f58d27266")
_SUSDS_ARB = Address.from_str("0xdDb46999F8891663a8F2828d25298f70416d7610")
_BLOCK = 350_000_000


def test_psm3_convert_to_shares_encodes_selector_and_args():
    """Calldata = SEL_CONVERT_TO_SHARES(4B) + asset(32B) + amount(32B)."""
    captured: dict = {}

    def fake_eth_call(chain, contract, data, block):
        captured["chain"] = chain
        captured["contract"] = contract
        captured["data"] = data
        captured["block"] = block
        # Return raw uint256 representing 9.35e17 — a realistic
        # shares-per-sUSDS quote (~0.935 PSM3 shares per 1 sUSDS).
        return "0x" + format(935_000_000_000_000_000, "064x")

    # Bypass the @cached decorator wrapping so we exercise the body directly.
    with patch.object(rpc, "eth_call", fake_eth_call), \
         patch.dict(rpc.os.environ, {"SETTLE_NO_CACHE": "1"}):
        out = rpc.psm3_convert_to_shares(
            Chain.ARBITRUM, _PSM3_ARB, _SUSDS_ARB, 10**18, _BLOCK,
        )

    assert out == 935_000_000_000_000_000
    assert captured["chain"] == Chain.ARBITRUM
    assert captured["contract"] == _PSM3_ARB
    assert captured["block"] == _BLOCK
    data = captured["data"]
    # 4-byte selector + two 32-byte args = 0x + 4 + 64 + 64 = 132 chars
    assert data.startswith(rpc.SEL_PSM3_CONVERT_TO_SHARES)
    assert len(data) == 2 + 8 + 64 + 64
    # Address-arg is left-padded with zeros, ends with the lowercased hex.
    asset_arg = data[10:74]
    assert asset_arg.startswith("0" * 24)  # 12 bytes of leading zeros
    assert asset_arg.endswith(_SUSDS_ARB.hex.replace("0x", "").lower())
    # Uint-arg is the amount.
    amount_arg = data[74:138]
    assert int(amount_arg, 16) == 10**18


def test_rpc_psm3_source_delegates_convert_to_shares():
    """``RPCPsm3Source.convert_to_shares`` is a thin wrapper over the cached
    ``rpc.psm3_convert_to_shares`` — verifies args round-trip."""
    seen: dict = {}

    def fake(chain, psm3, asset, amount, block):
        seen.update(
            chain=chain, psm3=psm3, asset=asset, amount=amount, block=block,
        )
        return 42

    src = RPCPsm3Source()
    with patch.object(rpc, "psm3_convert_to_shares", fake):
        out = src.convert_to_shares(
            chain="arbitrum",
            psm3=_PSM3_ARB.value,
            asset=_SUSDS_ARB.value,
            amount=10**18,
            block=_BLOCK,
        )

    assert out == 42
    assert seen["chain"] == Chain.ARBITRUM
    assert seen["psm3"] == _PSM3_ARB
    assert seen["asset"] == _SUSDS_ARB
    assert seen["amount"] == 10**18
    assert seen["block"] == _BLOCK


def test_dune_psm3_source_delegates_convert_to_shares_to_rpc():
    """``DunePsm3Source.convert_to_shares`` has no Dune preload to consult —
    it falls through to the cached RPC helper exactly like RPCPsm3Source."""
    seen: dict = {}

    def fake(chain, psm3, asset, amount, block):
        seen.update(
            chain=chain, psm3=psm3, asset=asset, amount=amount, block=block,
        )
        return 7

    src = DunePsm3Source()
    with patch.object(rpc, "psm3_convert_to_shares", fake):
        out = src.convert_to_shares(
            chain="base",
            psm3=_PSM3_ARB.value,
            asset=_SUSDS_ARB.value,
            amount=10**18,
            block=_BLOCK,
        )

    assert out == 7
    assert seen["chain"] == Chain.BASE
