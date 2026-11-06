"""Versioned dashboard projections over frozen calculation views; no providers."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import Depends, HTTPException, Query, Request

from ..revenue.verification import PRIMES, digest
from . import revenue

View = Literal["settlement", "reporting"]
TOTAL_FIELDS = ("prime_agent_revenue", "agent_rate", "distribution_rewards", "chronicle_points",
                "gar", "prime_agent_total_revenue", "sky_revenue", "monthly_pnl", "sde_revenue",
                "curve_susds_spread", "psm3_susds_spread", "psm3_susds_appreciation")


def project(record, view):
    saved = record["result"]
    views = saved.get("venue_views", {})
    if views.get("schema_version") != "2.0" or view not in views:
        raise HTTPException(409, "Independent venue views unavailable for this revision; publish a fresh calculation",
                            headers={"Cache-Control": "no-store"})
    rows = views[view]
    result = {
        "view": view, "period": saved["period"], "venues": rows,
        "complete": all(r["status"] != "unavailable" for r in rows),
        "totals": {k: saved[k] for k in TOTAL_FIELDS if k in saved} if view == "settlement" else None,
        "aggregation": ("MSC totals include prime-level components beyond venue income" if view == "settlement"
                        else "No consolidated total: holdings, savings vaults and incentive claims can overlap"),
    }
    if view == "settlement":
        result["totals"]["prime_agent_total_revenue"] = str(sum(
            (Decimal(saved.get(k, "0")) for k in
             ("prime_agent_revenue", "agent_rate", "distribution_rewards", "chronicle_points", "gar")),
            Decimal(0),
        ))
    metadata = {k: v for k, v in record.items() if k not in {"result", "result_hash"}}
    return {**metadata, "source_result_hash": record.get("result_hash"),
            "source_schema_version": revenue.with_result_schema(record)["result_schema_version"],
            "view_hash": digest(result), "result": result}


def register_views(app, get_reader, document_response):
    def validate(prime, cutoff=None):
        if prime not in PRIMES:
            raise HTTPException(404, "Unknown prime")
        if cutoff is not None and cutoff >= revenue.today():
            raise HTTPException(422, "cutoff must be a completed UTC day")

    def read(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            raise HTTPException(503, "Daily revenue store unavailable",
                                headers={"Cache-Control": "no-store"}) from None

    def document(request, reader, view, prime, cutoff=None, revision=None):
        validate(prime, cutoff)
        record, attempt = read(reader.revenue_bundle, prime, cutoff=cutoff, revision=revision)
        if record is None:
            raise HTTPException(404, "No published daily estimate for this selection",
                                headers={"Cache-Control": "no-store"})
        return document_response(request, {
            "schema_version": "2.0", "prime": prime, "view": view,
            "cadence": "daily", "estimate_basis": "month_to_date",
            "data": project(record, view),
            "freshness": revenue.freshness(record, cutoff or revenue.today() - timedelta(days=1)),
            "latest_attempt": attempt,
        })

    @app.get("/v2/venues/{prime}")
    def venues(prime: str, request: Request):
        from ..domain.config import load_prime_by_id
        validate(prime)
        config = load_prime_by_id(prime)
        return document_response(request, {
            "schema_version": "2.0", "prime": prime, "configuration": "current",
            "venues": [{"venue_id": v.id, "label": v.label, "chain": v.chain.value,
                        "pricing_category": v.pricing_category.value, "skip": v.skip,
                        "start_date": str(v.start_date) if v.start_date else None,
                        "settlement": {"enabled": v.settles, "method": v.settlement_view.method,
                                       "source_venue": v.settlement_view.source_venue,
                                       "payer": v.settlement_view.payer},
                        "reporting": {"enabled": v.reporting_view.enabled, "method": v.reporting_view.method,
                                      "pending_reason": v.reporting_view.pending_reason}}
                       for v in config.venues],
        })

    @app.get("/v2/{view}/{prime}/latest")
    def latest(view: View, prime: str, request: Request, r=Depends(get_reader)):  # noqa: B008
        return document(request, r, view, prime)

    @app.get("/v2/{view}/{prime}/at/{cutoff}")
    def at(view: View, prime: str, cutoff: date, request: Request,
           revision: str | None = Query(None, pattern=r"^[0-9a-f]{64}$"),
           r=Depends(get_reader)):  # noqa: B008
        return document(request, r, view, prime, cutoff, revision)

    @app.get("/v2/{view}/{prime}/history")
    def history(view: View, prime: str, request: Request, start: date | None = None,
                end: date | None = None, limit: int = Query(90, ge=1, le=90),
                r=Depends(get_reader)):  # noqa: B008
        end = end or revenue.today() - timedelta(days=1)
        start = start or end - timedelta(days=min(89, end.toordinal() - 1))
        validate(prime, end)
        if start > end or (end - start).days >= 90:
            raise HTTPException(422, "history window must contain 1 to 90 days")
        records = read(r.revenue_history, prime, start=start, end=end, limit=limit)
        results = []
        for record in records:
            try:
                results.append({"availability": "available", **project(record, view)})
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                results.append({"availability": "unavailable", "cutoff": record["cutoff"],
                                "revision_id": record["revision_id"], "reason": exc.detail})
        return document_response(request, {
            "schema_version": "2.0", "prime": prime, "view": view,
            "cadence": "daily", "estimate_basis": "month_to_date", "results": results,
            "order": "cutoff descending", "limit": limit, "start": str(start), "end": str(end),
            "missing_dates": "unavailable; never interpret as zero",
        })
