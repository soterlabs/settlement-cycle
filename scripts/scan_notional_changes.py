"""Scan on-chain principal flows against configured notional schedules.

For each prime × venue with a ``notional_principal_usd`` entry, queries
on-chain USDC (or whatever ``cash_distributions[*].token`` is configured)
flows between the prime's ALM and the known counterparty payer addresses
over the target settlement month, then cross-references each significant
flow against the schedule's transitions. Flags candidate disbursement /
repayment events that aren't reflected in the YAML so an operator can
update the schedule before running settlement.

The script is read-only — it does not modify any YAML config. Output is
a human-readable table; non-zero exit when at least one candidate event
is found.

Usage:
    PYTHONPATH=src python3 scripts/scan_notional_changes.py \\
        --month 2026-05 [--primes grove] [--threshold 1000000]

Required env: ``DUNE_API_KEY`` (Dune ``inflow_by_counterparty`` query),
``ETH_RPC`` (and any other chain RPC the configured venues use).

How "matched to schedule" works:
    For each schedule transition ``(start_date, amount)`` falling inside
    the period, the expected on-chain signed flow is ``-Δamount``: a
    disbursement (notional ↑) corresponds to an ALM-outflow (signed < 0);
    a repayment (notional ↓) to an ALM-inflow (signed > 0). A row matches
    a transition when its date is within ±7 days and its amount is within
    1% of the expected magnitude.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from settle.domain.config import load_prime  # noqa: E402
from settle.domain.primes import Chain, Prime, Venue  # noqa: E402
from settle.normalize.sources.dune_balances import DuneBalanceSource  # noqa: E402
from settle.normalize.sources.rpc_block_resolver import RPCBlockResolver  # noqa: E402

_PRIMES = {
    "obex":  _REPO / "config" / "obex.yaml",
    "grove": _REPO / "config" / "grove.yaml",
    "spark": _REPO / "config" / "spark.yaml",
}

_DATE_TOL_DAYS = 7
_AMOUNT_TOL_PCT = Decimal("0.01")  # 1%

_log = logging.getLogger("scan_notional")


def _month_bounds(yyyymm: str) -> tuple[date, date]:
    y, m = yyyymm.split("-")
    yi, mi = int(y), int(m)
    start = date(yi, mi, 1)
    end = (
        date(yi + 1, 1, 1) - timedelta(days=1)
        if mi == 12
        else date(yi, mi + 1, 1) - timedelta(days=1)
    )
    return start, end


def _scheduled_transitions(
    schedule: tuple,
    period_start: date,
    period_end: date,
) -> list[tuple[date, Decimal]]:
    """Return ``(date, expected_signed_amount)`` per schedule step inside
    the period. ``expected_signed_amount`` is what ``signed_amount`` from
    ``inflow_by_counterparty`` should show on that date (negative when
    notional rises, positive when it falls).
    """
    sorted_sched = sorted(schedule, key=lambda e: e.start_date)
    out: list[tuple[date, Decimal]] = []
    prev_amount = Decimal("0")
    for entry in sorted_sched:
        if entry.start_date > period_end:
            break
        if period_start <= entry.start_date <= period_end:
            delta = entry.amount - prev_amount
            out.append((entry.start_date, -delta))
        prev_amount = entry.amount
    return out


def _counterparties_by_chain(venue: Venue) -> dict[Chain, dict]:
    """Group ``cash_distributions`` by chain: chain → {token: bytes,
    payers: set[bytes]}. Returns empty dict when no distributions are
    configured."""
    out: dict[Chain, dict] = {}
    for cd in venue.cash_distributions:
        chain = cd.chain or venue.chain
        entry = out.setdefault(chain, {"token": cd.token.value, "payers": set()})
        if entry["token"] != cd.token.value:
            _log.warning(
                "venue %s: multiple distribution tokens on %s — using first (%s); "
                "skipping payer %s (%s).",
                venue.id, chain.value,
                "0x" + entry["token"].hex(),
                "0x" + cd.payer.value.hex(),
                "0x" + cd.token.value.hex(),
            )
            continue
        entry["payers"].add(cd.payer.value)
    return out


def _match_to_schedule(
    row_date: date,
    row_amount: Decimal,
    transitions: list[tuple[date, Decimal]],
) -> tuple[date, Decimal] | None:
    """Return the matching schedule transition, or None if no match."""
    for sched_date, sched_amount in transitions:
        if abs((row_date - sched_date).days) > _DATE_TOL_DAYS:
            continue
        if sched_amount == 0:
            continue
        ratio = abs(row_amount - sched_amount) / abs(sched_amount)
        if ratio <= _AMOUNT_TOL_PCT:
            return (sched_date, sched_amount)
    return None


def _scan_venue(
    prime: Prime,
    venue: Venue,
    period_start: date,
    period_end: date,
    threshold: Decimal,
    *,
    block_resolver: RPCBlockResolver,
    balance_source: DuneBalanceSource,
) -> list[dict]:
    """Scan one venue. Returns one finding row per (counterparty × day)
    flow above ``threshold``. Each row carries the matched schedule
    transition or None."""
    transitions = _scheduled_transitions(
        venue.notional_principal_usd, period_start, period_end,
    )
    chains_to_cps = _counterparties_by_chain(venue)
    if not chains_to_cps:
        _log.warning(
            "%s: notional_principal_usd configured but no cash_distributions to "
            "infer counterparty addresses — skipping. Add cash_distributions "
            "entries or extend this script to take explicit counterparty args.",
            venue.id,
        )
        return []
    eom_anchor = datetime.combine(period_end, time.max, tzinfo=timezone.utc)
    findings: list[dict] = []
    for chain, info in chains_to_cps.items():
        holder = prime.alm.get(chain)
        if holder is None:
            _log.warning(
                "%s: chain %s in cash_distributions but no ALM address configured "
                "on prime; skipping that leg.", venue.id, chain.value,
            )
            continue
        pin_block = block_resolver.block_at_or_before(chain.value, eom_anchor)
        df = balance_source.inflow_by_counterparty(
            chain.value, info["token"], holder.value, period_start, pin_block,
        )
        if df.empty:
            continue
        period_mask = df["block_date"].apply(
            lambda d: period_start <= d <= period_end
        )
        # Watch the yield payers AND any declared principal relay. Keying off
        # cash_distributions alone made this scan structurally blind to the
        # very event it exists to catch: Grove E42's principal moved through
        # 0x3E23311f…, not its yield payer, so a repayment would print "No
        # significant principal flows detected" and exit 0 — a false all-clear
        # on an open-ended $304M notional.
        payer_set = set(info["payers"]) | {
            a.value for a in venue.notional_counterparties
        }
        for _, row in df.loc[period_mask].iterrows():
            cp = row["counterparty"]
            if cp not in payer_set:
                continue
            amt = row["signed_amount"]
            if abs(amt) < threshold:
                continue
            findings.append({
                "prime": prime.id,
                "venue": venue.id,
                "chain": chain.value,
                "date": row["block_date"],
                "counterparty": "0x" + cp.hex(),
                "signed_amount": amt,
                "matched": _match_to_schedule(row["block_date"], amt, transitions),
            })
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Scan on-chain principal-flow events vs configured notional "
            "schedules for each venue with notional_principal_usd set."
        ),
    )
    parser.add_argument("--month", required=True, help="settlement month YYYY-MM")
    parser.add_argument(
        "--primes", default=None,
        help="comma-separated prime IDs (default: " + ",".join(_PRIMES) + ")",
    )
    parser.add_argument(
        "--threshold", type=Decimal, default=Decimal("1000000"),
        help="minimum |signed_amount| (USD) to flag — default 1,000,000",
    )
    parser.add_argument(
        "--log-level", default="INFO", help="Python logging level (default INFO)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    primes = (
        [p.strip() for p in args.primes.split(",")]
        if args.primes else list(_PRIMES)
    )
    bad = [p for p in primes if p not in _PRIMES]
    if bad:
        print(f"Unknown prime(s): {bad}. Choose from {list(_PRIMES)}", file=sys.stderr)
        return 1

    period_start, period_end = _month_bounds(args.month)
    block_resolver = RPCBlockResolver()
    balance_source = DuneBalanceSource()

    findings: list[dict] = []
    scanned = 0
    for prime_id in primes:
        prime = load_prime(_PRIMES[prime_id])
        for venue in prime.venues:
            if not venue.notional_principal_usd:
                continue
            scanned += 1
            findings.extend(_scan_venue(
                prime, venue, period_start, period_end, args.threshold,
                block_resolver=block_resolver,
                balance_source=balance_source,
            ))

    print()
    print("=" * 130)
    print(
        f"NOTIONAL-CHANGE SCAN — {args.month} "
        f"({period_start.isoformat()} → {period_end.isoformat()})  "
        f"threshold ≥ ${args.threshold:,}"
    )
    print(f"  primes: {primes}  scanned: {scanned} venue(s) with notional configured")
    print("=" * 130)

    if scanned == 0:
        print("No venues with notional_principal_usd configured in the selected "
              "primes. Nothing to scan.")
        return 0

    if not findings:
        print("No significant principal flows detected against configured "
              "notional schedules.")
        print("Schedules may still be stale — verify against governance forum / "
              "counterparty workbooks before running settlement.")
        return 0

    header = (
        f"{'prime':<6} {'venue':<6} {'chain':<10} {'date':<11} "
        f"{'counterparty':<46} {'signed_amount':>18}  {'verdict'}"
    )
    print(header)
    print("-" * 130)
    candidates = 0
    for f in findings:
        if f["matched"]:
            sched_date, sched_amt = f["matched"]
            verdict = (
                f"matches schedule {sched_date.isoformat()} (Δ=${sched_amt:,.0f})"
            )
        else:
            verdict = "CANDIDATE — not in schedule"
            candidates += 1
        print(
            f"{f['prime']:<6} {f['venue']:<6} {f['chain']:<10} "
            f"{f['date'].isoformat():<11} {f['counterparty']:<46} "
            f"${f['signed_amount']:>16,.2f}  {verdict}"
        )
    print("=" * 130)

    if candidates:
        print(
            f"{candidates} candidate(s) require operator review. Update the "
            "venue's notional_principal_usd schedule in the prime YAML if "
            "these are legitimate principal movements (negative = disbursement, "
            "positive = repayment)."
        )
        return 1
    print("All significant flows match the configured schedule.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
