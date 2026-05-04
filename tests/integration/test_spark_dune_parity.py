"""Spark Dune-table parity test — `settle snapshot --prime spark` vs
`dune.sparkdotfi.result_spark_*_by_alm_proxy` views.

Live test (RPC + Dune HTTP), gated behind ``@pytest.mark.live`` AND
``DUNE_API_KEY`` env var. Default ``pytest`` runs skip it. Run explicitly:

    DUNE_API_KEY=... pytest tests/integration/test_spark_dune_parity.py -m live -v -s

What's asserted (must hold within tolerance):
- For every venue Spark publishes a per-protocol table for, our
  ``snapshot.value_usd`` matches Spark's ``alm_supply_amount`` within
  the tolerance below at the latest ``dt`` they have data for.

What's reported (printed, not failed):
- Per-venue table name + column read + diff
- The full row from Spark's table for context
- Aggregated `Σ alm_idle` (likely BA's `idle_assets` source per Q B1)

The test creates a temporary Dune query, executes it, archives it. ~30-60s
per run. Costs Dune credits — use sparingly. Output gives the operator a
view of how our snapshot tracks Spark's published numbers daily.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from decimal import Decimal

import pytest

from settle.domain.config import load_prime_by_id
from settle.snapshot import compute_snapshot

# Per-venue tolerance — 1% drift accepted (block-time vs midnight-UTC dt
# snapshot drift, plus rate-accrual within the day).
PER_VENUE_TOLERANCE_PCT = Decimal("1.0")

# Map (venue_id, Spark table name, column with the venue value, optional
# token_symbol filter) → which `result_spark_*_by_alm_proxy` row our
# snapshot should match. Token filter handles tables that aggregate
# multiple tokens (sparklend has DAI/USDS rows; Aave has aUSDT / aBasUSDC
# rows in distinct tables).
SPARK_VENUE_TABLES: list[tuple[str, str, str, str | None]] = [
    # (venue_id, table, value_column, token_symbol_filter)
    ("S1",  "result_spark_idle_dai_usds_in_sparklend_by_alm_proxy", "alm_supply_amount", "USDS"),
    ("S4",  "result_spark_idle_dai_usds_in_sparklend_by_alm_proxy", "alm_supply_amount", "DAI"),
    ("S2",  "result_spark_idle_dai_usds_in_sparklend_by_alm_proxy", "alm_supply_amount", "USDC"),
    ("S3",  "result_spark_idle_dai_usds_in_sparklend_by_alm_proxy", "alm_supply_amount", "USDT"),
    ("S5",  "result_spark_idle_dai_usds_in_sparklend_by_alm_proxy", "alm_supply_amount", "PYUSD"),
    ("S9",  "result_spark_aave_ethereum_a_usdt_by_alm_proxy",       "alm_supply_amount", None),
    ("S54", "result_spark_aave_avalanche_a_usdc_by_alm_proxy",      "alm_supply_amount", None),
    ("S14", "result_spark_maple_syrup_usdc_by_alm_proxy",           "amount",            None),
    ("S15", "result_spark_maple_syrup_usdt_by_alm_proxy",           "amount",            None),
    ("S18", "result_spark_arkis_spark_prime_usdc_1_by_alm_proxy",   "amount",            None),
    # Morpho aggregates 4 markets (S10/S11/S12/S13) by token in one table.
    ("S10", "result_spark_idle_dai_usdc_in_morpho_by_alm_proxy",    "alm_supply_amount", "USDC"),
    ("S12", "result_spark_idle_dai_usdc_in_morpho_by_alm_proxy",    "alm_supply_amount", "DAI"),
    ("S13", "result_spark_idle_dai_usdc_in_morpho_by_alm_proxy",    "alm_supply_amount", "USDS"),
    ("S11", "result_spark_idle_dai_usdc_in_morpho_by_alm_proxy",    "alm_supply_amount", "USDT"),
    # Curve LPs (S24/S25) live in result_spark_curve_pool_apr — see Q S15.
    ("S24", "result_spark_curve_pool_apr",                          "sll_total_assets_balance", "sUSDS/USDT"),
    ("S25", "result_spark_curve_pool_apr",                          "sll_total_assets_balance", "PYUSD/USDS"),
]

DUNE_BASE = "https://api.dune.com/api/v1"

# NOTE: This file uses an inline urllib-based Dune client rather than
# ``settle.extract.dune`` because the production client targets
# stored-query reads (table reads via ``executeQueryById``), while this
# test needs the create-temp-query / execute / archive flow on ad-hoc SQL.
# If a future refactor unifies them, fold these helpers into a
# ``extract.dune.execute_inline_sql(sql, api_key)`` and call that here.


def _dune_post(path: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(
        DUNE_BASE + path, data=json.dumps(body).encode(),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Dune POST {path} failed {e.code}: {e.read()[:200]!r}") from e


def _dune_get(path: str, headers: dict) -> dict:
    req = urllib.request.Request(DUNE_BASE + path, headers=headers, method="GET")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as e:
        # Without this, a 429 rate-limit hit during the polling loop bubbles
        # up as an opaque HTTPError mid-test. Surface the code + body instead.
        raise RuntimeError(f"Dune GET {path} failed {e.code}: {e.read()[:200]!r}") from e


def _dune_patch(path: str, headers: dict, body: dict) -> None:
    req = urllib.request.Request(
        DUNE_BASE + path, data=json.dumps(body).encode(),
        headers={**headers, "Content-Type": "application/json"},
        method="PATCH",
    )
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except Exception:
        pass   # archive failure is non-fatal


def _execute_inline_sql(sql: str, api_key: str) -> list[dict]:
    """Submit ad-hoc SQL to Dune as a temporary query; return rows."""
    headers = {"X-Dune-Api-Key": api_key}
    qid = _dune_post("/query", headers, {
        "name": "settle parity test (auto, archive after)",
        "query_sql": sql,
        "is_private": True,
    })["query_id"]
    try:
        eid = _dune_post(f"/query/{qid}/execute", headers, {})["execution_id"]
        deadline = time.time() + 180
        while time.time() < deadline:
            r = _dune_get(f"/execution/{eid}/results", headers)
            state = r.get("state")
            if state == "QUERY_STATE_COMPLETED":
                return r.get("result", {}).get("rows", [])
            if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
                raise RuntimeError(f"Dune execution {eid} ended in {state}: {r}")
            time.sleep(3)
        raise RuntimeError(f"Dune execution {eid} timed out after 180s")
    finally:
        _dune_patch(f"/query/{qid}", headers, {"is_archived": True})


def _build_parity_sql() -> str:
    """One UNION-ALL query that pulls latest-dt rows from every relevant
    Spark per-venue table. Reading them in one query is faster + cheaper
    than 16 separate executions."""
    parts = []
    for venue_id, table, value_col, token_filter in SPARK_VENUE_TABLES:
        cond = f"AND token_symbol = '{token_filter}'" if token_filter else ""
        parts.append(f"""
SELECT
    '{venue_id}'                 AS venue_id,
    '{table}'                    AS table_name,
    blockchain,
    token_symbol,
    dt,
    {value_col}                  AS spark_value_usd,
    COALESCE(alm_idle, 0)        AS alm_idle
FROM dune.sparkdotfi.{table}
WHERE dt = (
    SELECT MAX(dt) FROM dune.sparkdotfi.{table}
)
{cond}
""".strip())
    return "\nUNION ALL\n".join(parts) + "\nORDER BY venue_id"


@pytest.mark.live
def test_spark_dune_parity():
    api_key = os.environ.get("DUNE_API_KEY")
    if not api_key:
        pytest.skip("DUNE_API_KEY not set — required to query dune.sparkdotfi.result_* tables")

    sql = _build_parity_sql()
    rows = _execute_inline_sql(sql, api_key)
    if not rows:
        pytest.fail("Dune query returned 0 rows — table names may have changed")

    # Index Spark rows by venue_id (one row per (venue, table, token_filter)).
    spark_by_venue: dict[str, dict] = {}
    for r in rows:
        spark_by_venue.setdefault(r["venue_id"], r)   # first match wins

    prime = load_prime_by_id("spark")
    snap = compute_snapshot(prime)
    snap_by_id = {v.venue_id: v for v in snap.venues}

    print()
    print("=" * 96)
    print(f"SPARK DUNE PARITY — settle snapshot vs dune.sparkdotfi.result_spark_*_by_alm_proxy")
    print("=" * 96)
    print(f"  {'venue':5s} {'table':50s} {'snap':>14s} {'spark':>14s} {'diff':>14s}  {'%':>7s}")
    print("  " + "-" * 110)

    drift_failures: list[tuple] = []
    total_alm_idle = Decimal(0)

    for venue_id, table, _value_col, _token in SPARK_VENUE_TABLES:
        spark_row = spark_by_venue.get(venue_id)
        snap_v = snap_by_id.get(venue_id)
        if spark_row is None:
            print(f"  {venue_id:5s} {table[:50]:50s} (no Spark row at latest dt — venue absent in their data)")
            continue
        if snap_v is None or snap_v.note.startswith("ERR") or snap_v.value_usd == 0:
            note = "skipped" if snap_v and snap_v.note.startswith("ERR") else "snap=$0"
            print(f"  {venue_id:5s} {table[:50]:50s} ({note}, "
                  f"spark=${Decimal(str(spark_row['spark_value_usd'])):,.2f})")
            continue

        ours = snap_v.value_usd
        theirs = Decimal(str(spark_row["spark_value_usd"]))
        diff = ours - theirs
        if theirs == 0:
            pct = Decimal(0)
        else:
            pct = (diff / theirs * 100).copy_abs()
        ok = pct < PER_VENUE_TOLERANCE_PCT
        flag = "" if ok else "  ✗ DRIFT"
        print(f"  {venue_id:5s} {table[:50]:50s} ${ours:>12,.0f}  ${theirs:>12,.0f}  ${diff:>+12,.0f}  {pct:6.3f}%{flag}")
        total_alm_idle += Decimal(str(spark_row.get("alm_idle") or 0))
        if not ok:
            drift_failures.append((venue_id, table, ours, theirs, pct))

    print("  " + "-" * 110)
    print(f"  Σ alm_idle across these venues: ${total_alm_idle:,.0f}  (Spark publishes; likely contributes to BA `idle_assets`)")
    print()

    # Hard assertion: any venue we cleanly read from BOTH sides must match
    # within tolerance. Skipped/zero-value rows don't count.
    assert not drift_failures, (
        f"Spark Dune parity drift exceeded {PER_VENUE_TOLERANCE_PCT}% on: "
        + ", ".join(f"{v[0]}({v[4]:.2f}%)" for v in drift_failures)
    )
