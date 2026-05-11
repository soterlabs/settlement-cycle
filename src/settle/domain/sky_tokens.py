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


# ---------------------------------------------------------------------------
# Per-L2 token registry — USDC + USDS + sUSDS on each chain that hosts a Spark
# PSM3. Used by ``psm3_leg_breakdown`` (see ``compute/monthly_pnl.py``) to
# decompose PSM3's USDS-equivalent value into its three constituent reserves
# (USDC / USDS / sUSDS) for the leg-split rules in PRD §17.11:
#   - USDS leg  → subtracted from utilized (BR-reimbursed)
#   - USDC leg  → Sky Direct Exposure (Sky takes actual yield, prime keeps 0;
#                 utilized NOT reduced for this slice)
#   - sUSDS leg → utilized NOT reduced; prime earns 30 bps spread Prime Revenue
#                 on its USDS-equivalent value (sUSDS already returns SSR via
#                 share price; BR = SSR + 30 bps, so 30 bps is the residual)
#
# Discovered via Dune queries 7468346 + 7468351 (Sky-decoded tables + tokens.transfers).
# sUSDS on L2s does NOT expose ``convertToAssets`` — pps is read from the
# Ethereum sUSDS at the matching EoD block (the L2 sUSDS is a 1:1 bridge of
# Ethereum sUSDS; verified to 4 decimals across all 4 L2 chains).
PSM3_LEG_TOKENS: dict[Chain, dict[str, Token]] = {
    Chain.BASE: {
        "USDC":  Token(Chain.BASE, Address.from_str("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"), "USDC",  6),
        "USDS":  Token(Chain.BASE, Address.from_str("0x820c137fa70c8691f0e44dc420a5e53c168921dc"), "USDS",  18),
        "sUSDS": Token(Chain.BASE, Address.from_str("0x5875eee11cf8398102fdad704c9e96607675467a"), "sUSDS", 18),
    },
    Chain.ARBITRUM: {
        "USDC":  Token(Chain.ARBITRUM, Address.from_str("0xaf88d065e77c8cc2239327c5edb3a432268e5831"), "USDC",  6),
        "USDS":  Token(Chain.ARBITRUM, Address.from_str("0x6491c05a82219b8d1479057361ff1654749b876b"), "USDS",  18),
        "sUSDS": Token(Chain.ARBITRUM, Address.from_str("0xddb46999f8891663a8f2828d25298f70416d7610"), "sUSDS", 18),
    },
    Chain.OPTIMISM: {
        "USDC":  Token(Chain.OPTIMISM, Address.from_str("0x0b2c639c533813f4aa9d7837caf62653d097ff85"), "USDC",  6),
        "USDS":  Token(Chain.OPTIMISM, Address.from_str("0x4f13a96ec5c4cf34e442b46bbd98a0791f20edc3"), "USDS",  18),
        "sUSDS": Token(Chain.OPTIMISM, Address.from_str("0xb5b2dc7fd34c249f4be7fb1fcea07950784229e0"), "sUSDS", 18),
    },
    Chain.UNICHAIN: {
        "USDC":  Token(Chain.UNICHAIN, Address.from_str("0x078d782b760474a361dda0af3839290b0ef57ad6"), "USDC",  6),
        "USDS":  Token(Chain.UNICHAIN, Address.from_str("0x7e10036acc4b56d4dfca3b77810356ce52313f9c"), "USDS",  18),
        "sUSDS": Token(Chain.UNICHAIN, Address.from_str("0xa06b10db9f390990364a3984c04fadf1c13691b5"), "sUSDS", 18),
    },
}
