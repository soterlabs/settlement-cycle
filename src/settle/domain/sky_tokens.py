"""Canonical Sky-protocol constants.

These are global Sky invariants (tokens, rate-history anchors) needed by Compute
regardless of which prime is being settled. Per-chain entries — Phase 1 covers
Ethereum only.
"""

from __future__ import annotations

from datetime import date

from .primes import Address, Chain, Token

# Earliest known SP-BEAM `file()` call (per RULES.md Rule 2) was 2024-09-17 at
# 6.25% APY. We anchor SSR-history queries a couple of weeks before so the
# carry-forward in Compute always has a baseline rate effective on or before
# any prime's first month. This is a Sky-protocol invariant — every prime that
# launches **on or after** this date is correctly handled by the carry-forward
# logic. A prime with an earlier `start_date` would need this anchor moved back.
SSR_HISTORY_ANCHOR: date = date(2024, 9, 1)


# Address-to-(symbol, decimals) registry for par-stable tokens used as LP
# underlyings. Used by Cat F (Curve / Uni V3) pricing to resolve
# `pool.coins(i)` addresses into priced underlyings without an extra RPC call.
#
# Only par-stables included — yield-bearing LP underlyings (sUSDS, sUSDe) need
# recursive pricing (Phase 2.B+) and aren't valid here.
KNOWN_PAR_STABLES_ETHEREUM: dict[bytes, tuple[str, int]] = {
    bytes.fromhex("a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"): ("USDC", 6),
    bytes.fromhex("dc035d45d973e3ec169d2276ddab16f1e407384f"): ("USDS", 18),
    bytes.fromhex("6b175474e89094c44da98b954eedeac495271d0f"): ("DAI", 18),
    bytes.fromhex("dac17f958d2ee523a2206206994597c13d831ec7"): ("USDT", 6),
    bytes.fromhex("6c3ea9036406852006290770bedfcaba0e23a0e8"): ("PYUSD", 6),
    bytes.fromhex("8292bb45bf1ee4d140127049757c2e0ff06317ed"): ("RLUSD", 18),
    bytes.fromhex("00000000efe302beaa2b3e6e1b18d08d69a9012a"): ("AUSD",  6),
    bytes.fromhex("4c9edd5852cd905f086c759e8383e09bff1e68b3"): ("USDe", 18),
}


# Yield-bearing ERC-4626 tokens used as Curve / Uni V3 pool coins. Maps the
# 4626 vault address to (symbol, share_decimals, underlying_par_stable_address,
# underlying_decimals). Recursive pricing: ``convertToAssets(10**share_decimals)
# / 10**underlying_decimals * par_price_of_underlying``. Used by
# ``_curve_lp_unit_price`` so pools containing these (Spark sUSDSUSDT) can be
# priced without manual exclusion.
KNOWN_YIELD_BEARING_ETHEREUM: dict[bytes, tuple[str, int, bytes, int]] = {
    # sUSDS — Sky Savings vault (4626 over USDS). Used in S24 sUSDSUSDT Curve.
    bytes.fromhex("a3931d71877c0e7a3148cb7eb4463524fec27fbd"): (
        "sUSDS", 18,
        bytes.fromhex("dc035d45d973e3ec169d2276ddab16f1e407384f"),  # USDS
        18,
    ),
}

USDS_ETHEREUM = Token(
    chain=Chain.ETHEREUM,
    address=Address.from_str("0xdc035d45d973e3ec169d2276ddab16f1e407384f"),
    symbol="USDS",
    decimals=18,
)

sUSDS_ETHEREUM = Token(
    chain=Chain.ETHEREUM,
    address=Address.from_str("0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD"),
    symbol="sUSDS",
    decimals=18,
)
