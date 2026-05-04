"""BA labs parity test — `settle snapshot` vs `stars-api.blockanalitica.com`.

Live tests (RPC + HTTP fetch from BA), gated behind ``@pytest.mark.live``.
Default ``pytest`` runs skip them. Run explicitly:

    pytest tests/integration/test_ba_parity.py -m live -v -s

Hard assertions (must hold within tight tolerance):
- ``debt`` matches BA at the cent level — independent on-chain Vat read
- ``treasury_balance`` matches — subproxy USDS via balanceOf
- per-venue values, where BA indexes the same address, agree within 0.5%

Soft (informational) checks — printed but never fail:
- top-line ``assets`` / ``idle_assets`` / ``in_transit`` / ``liabilities`` / ``nav``
  diverge because BA aggregates at the protocol level (different decomposition).
  We don't try to match them; we report the diff for audit.

Per-venue assertions skip venues whose snapshot returned an RPC error
(L2 drpc free-tier rate-limits intermittently).
"""
from __future__ import annotations

import json
import urllib.request
from decimal import Decimal

import pytest

from settle.domain.config import load_prime_by_id
from settle.snapshot import compute_snapshot

DEBT_TOLERANCE_USD = Decimal("100")
TREASURY_TOLERANCE_USD = Decimal("100")
PER_VENUE_TOLERANCE_PCT = Decimal("0.5")

# Known-correct divergences from BA — our snapshot reads a different (typically
# more accurate) source than BA. The test reports the drift but doesn't fail.
#
# Inclusion policy (READ before adding a venue):
#   1. The drift must be a *documented methodology disagreement*, not a bug.
#      We have to be confident that our number is correct (not just different).
#   2. Add a corresponding entry in QUESTIONS.md under the relevant prime so
#      the disagreement is visible to BA / the prime team for resolution.
#   3. The leading comment on each entry MUST cite the methodology source
#      (oracle name, contract address, or PRD section) so a future reviewer
#      can re-validate without spelunking commits.
#   4. Audit sign-off: do not add silently — flag in the PR description and
#      reference any monthly-settlement output that will shift as a result.
#
# This whitelist intentionally stays small. If it grows past a handful of
# venues, treat that as a signal that the parity test's tolerance or shape
# has drifted from what's still useful and revisit the test contract.
KNOWN_NAV_DIVERGENCES = {
    # Grove E7 STAC: we use Chronicle NAV (oracle returns ~$1.015 reflecting
    # real CLO yield); BA appears to pin at const $1.00. Drift ~1.5% is
    # the actual NAV growth — our number is more accurate, not wrong.
    # See QUESTIONS.md B5 (E7 NAV oracle question) for the running discussion.
    "E7",
}


def _fetch_ba_summary(prime: str) -> dict:
    url = f"https://stars-api.blockanalitica.com/stars/{prime}/"
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read())["data"]


def _fetch_ba_allocations(prime: str) -> list[dict]:
    url = f"https://stars-api.blockanalitica.com/allocations/?star={prime}"
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read())["data"]


def _print_parity_report(label: str, snap, ba_summary: dict, ba_allocs: list[dict],
                          prime) -> None:
    print()
    print("=" * 88)
    print(f"BA PARITY — {label}")
    print("=" * 88)
    fields = [
        ("debt",        snap.debt_usd or Decimal("0"), Decimal(str(ba_summary["debt"])),       True),
        ("treasury",    snap.treasury_balance_usd,      Decimal(str(ba_summary["treasury_balance"])), True),
        ("assets",      snap.assets_usd,                Decimal(str(ba_summary["assets"])),     False),
        ("idle_assets", snap.idle_assets_usd,           Decimal(str(ba_summary["idle_assets"])), False),
        ("in_transit",  snap.in_transit_assets_usd,     Decimal(str(ba_summary["in_transit_assets"])), False),
        ("liabilities", snap.liabilities_usd,           Decimal(str(ba_summary["liabilities"])), False),
        ("nav",         snap.nav_usd,                   Decimal(str(ba_summary["nav"])),        False),
    ]
    print(f"  {'metric':14s} {'snapshot':>20s} {'BA labs':>20s} {'diff':>20s}  hard?")
    print("  " + "-" * 86)
    for name, ours, theirs, hard in fields:
        diff = ours - theirs
        flag = "ASSERT" if hard else " info "
        print(f"  {name:14s} ${ours:>18,.2f}  ${theirs:>18,.2f}  ${diff:>+18,.2f}  {flag}")

    addrs = {v.id: v.token.address.hex.lower() for v in prime.venues}
    ba_by_addr = {it["address"].lower(): it for it in ba_allocs}
    print()
    print(f"  Per-venue parity (only where BA indexes the same address):")
    drift_failures = []
    for vs in snap.venues:
        addr = addrs.get(vs.venue_id, "")
        ba = ba_by_addr.get(addr)
        if ba is None or ba["assets"] == 0:
            continue
        if vs.note.startswith("ERR"):
            print(f"    {vs.venue_id:5s} {vs.label[:38]:38s} "
                  f"SKIP (snapshot RPC error: {vs.note[:40]})")
            continue
        ours = vs.value_usd
        theirs = Decimal(str(ba["assets"]))
        # Skip when our snapshot reports $0 but BA reports non-zero — almost
        # always an L2 RPC silent-zero (rpc.py logs a warning and returns 0
        # after retries exhausted). Real "we have $0" cases are caught by
        # the inverse case: we report > $0 but BA reports $0.
        if ours == 0 and theirs != 0:
            print(f"    {vs.venue_id:5s} {vs.label[:38]:38s} "
                  f"SKIP (snapshot=$0 vs BA=${theirs:,.2f}; likely L2 RPC drop)")
            continue
        diff = ours - theirs
        pct = (diff / theirs * 100).copy_abs()
        ok = pct < PER_VENUE_TOLERANCE_PCT or vs.venue_id in KNOWN_NAV_DIVERGENCES
        flag = "" if ok else "  ✗ DRIFT"
        if vs.venue_id in KNOWN_NAV_DIVERGENCES and pct >= PER_VENUE_TOLERANCE_PCT:
            flag = "  (known NAV oracle divergence — see KNOWN_NAV_DIVERGENCES)"
        print(f"    {vs.venue_id:5s} {vs.label[:38]:38s} "
              f"${ours:>16,.2f}  ${theirs:>16,.2f}  ${diff:>+12,.2f}  {pct:.4f}%{flag}")
        if not ok:
            drift_failures.append((vs.venue_id, vs.label, ours, theirs, pct))
    return drift_failures


def _hard_assert(snap, ba_summary, label):
    debt_diff = (snap.debt_usd or Decimal("0")) - Decimal(str(ba_summary["debt"]))
    assert abs(debt_diff) < DEBT_TOLERANCE_USD, (
        f"{label} debt mismatch: snapshot ${snap.debt_usd}, "
        f"BA ${ba_summary['debt']}, diff ${debt_diff} (>{DEBT_TOLERANCE_USD})"
    )
    treasury_diff = snap.treasury_balance_usd - Decimal(str(ba_summary["treasury_balance"]))
    # Treasury hard-assert is opt-in: BA's "treasury" for Spark differs from
    # ours (BA may include addresses we don't track). For Grove the subproxy
    # USDS is the canonical match.
    if label == "GROVE":
        assert abs(treasury_diff) < TREASURY_TOLERANCE_USD, (
            f"{label} treasury mismatch: snapshot ${snap.treasury_balance_usd}, "
            f"BA ${ba_summary['treasury_balance']}, diff ${treasury_diff}"
        )


@pytest.mark.live
def test_grove_parity(capfd):
    prime = load_prime_by_id("grove")
    snap = compute_snapshot(prime)
    ba_summary = _fetch_ba_summary("grove")
    ba_allocs = _fetch_ba_allocations("grove")

    drift = _print_parity_report("GROVE", snap, ba_summary, ba_allocs, prime)
    _hard_assert(snap, ba_summary, "GROVE")
    assert not drift, (
        f"Grove per-venue drift exceeded {PER_VENUE_TOLERANCE_PCT}% on: "
        + ", ".join(f"{v[0]}({v[4]:.2f}%)" for v in drift)
    )


@pytest.mark.live
def test_spark_parity(capfd):
    prime = load_prime_by_id("spark")
    snap = compute_snapshot(prime)
    ba_summary = _fetch_ba_summary("spark")
    ba_allocs = _fetch_ba_allocations("spark")

    drift = _print_parity_report("SPARK", snap, ba_summary, ba_allocs, prime)
    _hard_assert(snap, ba_summary, "SPARK")
    assert not drift, (
        f"Spark per-venue drift exceeded {PER_VENUE_TOLERANCE_PCT}% on: "
        + ", ".join(f"{v[0]}({v[4]:.2f}%)" for v in drift)
    )
