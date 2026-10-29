"""Read-only, monthly allocation borrowing costs and yields."""

from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Query, Request

from ..revenue import allocations as store
from ..revenue.verification import PRIMES


class AllocationReaderMixin:
    def monthly_allocations(self, prime, month, *, revision=None):
        with self._pool.connection() as conn:
            return store.read(conn, prime, month, revision=revision)


def register(app, get_reader, document_response):
    @app.get("/v1/revenue/{prime}/monthly/{month}/allocations")
    def monthly_allocations(
        prime: str,
        month: str,
        request: Request,
        venue: str | None = Query(None, pattern=r"^[A-Za-z0-9_-]{1,80}$"),
        revision: str | None = Query(None, pattern=r"^[0-9a-f]{64}$"),
        reader=Depends(get_reader),  # noqa: B008
    ):
        if prime not in PRIMES:
            raise HTTPException(404, "Unknown prime", headers={"Cache-Control": "no-store"})
        try:
            _, end = store.month_bounds(month)
        except ValueError:
            raise HTTPException(422, "month must be YYYY-MM") from None
        if end >= datetime.now(UTC).date():
            raise HTTPException(422, "month must be a completed UTC month")
        try:
            document = reader.monthly_allocations(prime, month, revision=revision)
        except Exception:
            raise HTTPException(
                503, "Monthly allocation store unavailable", headers={"Cache-Control": "no-store"}
            ) from None
        if document is None:
            raise HTTPException(
                404,
                "No monthly allocation analytics for this selection",
                headers={"Cache-Control": "no-store"},
            )
        if venue is not None:
            rows = [r for r in document["allocations"] if r["venue_id"] == venue]
            if not rows:
                raise HTTPException(
                    404,
                    "Unknown allocation in this monthly result",
                    headers={"Cache-Control": "no-store"},
                )
            document = {**document, "venue": venue, "count": len(rows), "allocations": rows}
        return document_response(request, document)
