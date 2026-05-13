"""End-to-end test for the Merkl Claimed+Mint JOIN SQL.

The unit tests for ``_atoken_external_revenue_usd`` stub ``execute_query``,
so they cover dispatcher routing + parameter wiring but do NOT exercise
the SQL itself against Dune. The first iteration of
``merkl_claims_ethereum.sql`` returned $0 across the board in production
(wrong topic2 filter — see PRD §17.13 Update 2026-05-14 (b)) and no unit
test failed; this integration test guards against silent regressions of
that kind.

Pins the SQL's behaviour against the two on-chain Merkl claim txs known
to have credited Grove's Ethereum ALM in 2026:

  * Feb 6  (tx 0x8a81d6dd…704a) → E1 ≈ $821K + E3 ≈ $2.96M
  * Apr 24 (tx 0xd374d598…e3e7) → E1 ≈ $979K + E3 ≈ $1.41M

Total $6,175,678.65 ± $1 (allow rounding on Decimal conversion).

Live test, gated behind ``@pytest.mark.live`` AND ``DUNE_API_KEY``.
Default ``pytest`` runs skip it. Run explicitly:

    DUNE_API_KEY=... pytest tests/integration/test_merkl_claims_e2e.py -m live -v -s
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from settle.domain import Period
from settle.domain.config import load_prime
from settle.domain.primes import Chain
from settle.normalize.positions import _merkl_claims_revenue_usd

_REPO = Path(__file__).resolve().parents[2]
_MERKL_DISTRIBUTOR_HEX = "3ef3d8ba38ebe18db133cec108f4d14ce00dd9ae"

# Expected per-venue USD amounts from the on-chain claims, rounded to the
# cent. Source: Dune query 7489308 verified 2026-05-13 + helper E2E run
# 2026-05-14. Tolerance is $1 to absorb any Decimal-to-float quirks; the
# helper returns whole-cent Decimals so exact equality would also work.
_EXPECTED: dict[tuple[str, str], Decimal] = {
    ("2026-02", "E1"): Decimal("821306.03"),
    ("2026-02", "E3"): Decimal("2963561.64"),
    ("2026-04", "E1"): Decimal("978913.67"),
    ("2026-04", "E3"): Decimal("1411897.31"),
}
_TOLERANCE = Decimal("1")


@pytest.mark.live
def test_merkl_claims_e2e_matches_onchain_amounts():
    if not os.environ.get("DUNE_API_KEY"):
        pytest.skip("DUNE_API_KEY not set — required to hit ethereum.logs on Dune")

    prime = load_prime(_REPO / "config" / "grove.yaml")
    # Find the Merkl distributor entry in the prime's external_alm_sources.
    # Fail loudly rather than silently no-op if it's been removed — that
    # would mean the production path is no longer wired to capture rewards.
    eth_sources = prime.external_alm_sources.get(Chain.ETHEREUM, [])
    merkl = next(
        (a for a in eth_sources if a.value.hex() == _MERKL_DISTRIBUTOR_HEX),
        None,
    )
    assert merkl is not None, (
        f"Merkl distributor 0x{_MERKL_DISTRIBUTOR_HEX} missing from "
        "grove.yaml external_alm_sources.ethereum — rewards path is "
        "no longer wired."
    )

    venues_by_id = {v.id: v for v in prime.venues}
    periods = [
        ("2026-02", Period(start=date(2026, 2, 1), end=date(2026, 2, 28),
                           pin_blocks={Chain.ETHEREUM: 24_558_867})),
        ("2026-04", Period(start=date(2026, 4, 1), end=date(2026, 4, 30),
                           pin_blocks={Chain.ETHEREUM: 25_000_000})),
    ]

    print()
    print(f"Merkl E2E — ALM 0x{prime.alm[Chain.ETHEREUM].value.hex()}")

    actual: dict[tuple[str, str], Decimal] = {}
    for label, period in periods:
        for vid in ("E1", "E3"):
            venue = venues_by_id[vid]
            got = _merkl_claims_revenue_usd(prime, venue, period, merkl)
            actual[(label, vid)] = got
            exp = _EXPECTED[(label, vid)]
            print(f"  {label} {vid} {venue.token.symbol:<14}  "
                  f"${got:>16,.2f}  (expected ${exp:>14,.2f})")

    # Per-(period, venue) tolerance check: each amount must be within $1
    # of the expected value. This catches both SQL regressions (e.g. the
    # original topic2 filter that returned $0) and accidental Dune query
    # rebinds (e.g. someone updates the published-query body without
    # noticing the parameter contract changed).
    for key, exp in _EXPECTED.items():
        got = actual[key]
        diff = abs(got - exp)
        assert diff <= _TOLERANCE, (
            f"{key}: got ${got:,.2f}, expected ${exp:,.2f} "
            f"(diff ${diff:,.2f} > tolerance ${_TOLERANCE})"
        )

    # Grand-total sanity check — guards against per-venue numbers happening
    # to net out to something close to expected via offsetting bugs.
    total = sum(actual.values(), Decimal("0"))
    expected_total = sum(_EXPECTED.values(), Decimal("0"))
    assert abs(total - expected_total) <= _TOLERANCE * 4, (
        f"Grand total ${total:,.2f} vs expected ${expected_total:,.2f}"
    )
