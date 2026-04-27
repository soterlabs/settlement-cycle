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
