"""Stored cash-flow reporting only; HTTP reads never trigger chain collection."""

from datetime import date, timedelta

from fastapi import Depends, HTTPException, Query, Request, Response

from ..revenue.cash_flow import store
from ..revenue.cash_flow.config import layout
from ..revenue.cash_flow.project import annual, decode
from . import revenue


class CashFlowReaderMixin:
    def cash_flow_year(self, prime, year, revisions=None, as_of=None):
        with self._pool.connection() as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                return store.year_rows(conn, prime, year, revisions, as_of)

    def cash_flow_events(self, prime, month, revision, line, offset, limit):
        with self._pool.connection() as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                return store.events(conn, prime, month, revision, line, offset, limit)

    def cash_flow_balances(self, prime, month, revision):
        with self._pool.connection() as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                return store.balances(conn, prime, month, revision)


def register(app, get_reader, document_response):
    def scope(prime, year):
        if prime != "spark" or year != 2026:
            raise HTTPException(404, "Cash-flow policy is currently available for Spark 2026")

    def respond(request, result):
        response = document_response(request, result, max_age=30)
        tag = response.headers.get("etag")
        supplied = request.headers.get("if-none-match", "").strip()
        if (
            tag
            and response.status_code == 200
            and (
                supplied == "*"
                or tag in [t.strip().removeprefix("W/") for t in supplied.split(",")]
            )
        ):
            return Response(
                status_code=304,
                headers={"ETag": tag, "Cache-Control": response.headers["cache-control"]},
            )
        return response

    @app.get("/v3/reporting/cash-flow")
    def cash_flow(
        request: Request,
        prime: str,
        year: int = Query(ge=2020, le=9998),
        cutoff: date | None = None,
        revision: str | None = Query(None, max_length=4096),
        review: bool = False,
        r=Depends(get_reader),  # noqa: B008
    ):
        scope(prime, year)
        if cutoff and revision:
            raise HTTPException(422, "Select a cutoff or an immutable selection, not both")
        selection = None
        if revision:
            try:
                selection = decode(revision, prime, year)
                cutoff = date.fromisoformat(selection["as_of"])
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        as_of = cutoff or min(revenue.today() - timedelta(days=1), date(year, 12, 31))
        if as_of.year != year or as_of >= revenue.today():
            raise HTTPException(
                422, "Cash-flow cutoff must be a completed UTC day in the selected year"
            )
        try:
            rows = r.cash_flow_year(
                prime,
                year,
                [v for v in selection["revisions"] if v] if selection else None,
                as_of=as_of,
            )
            result = annual(prime, year, as_of, rows, selection=selection, review=review)
        except (LookupError, ValueError) as exc:
            raise HTTPException(404, str(exc), headers={"Cache-Control": "no-store"}) from None
        except Exception:
            raise HTTPException(
                503, "Cash-flow reporting store unavailable", headers={"Cache-Control": "no-store"}
            ) from None
        return respond(request, result)

    @app.get("/v3/reporting/cash-flow/balances")
    def balances(
        request: Request,
        prime: str,
        month: str = Query(pattern=r"^2026-(0[1-9]|1[0-2])$"),
        revision: str = Query(pattern=r"^[a-f0-9]{64}$"),
        r=Depends(get_reader),  # noqa: B008
    ):
        scope(prime, int(month[:4]))
        try:
            result = r.cash_flow_balances(prime, month, revision)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except Exception:
            raise HTTPException(
                503, "Cash-flow balances store unavailable", headers={"Cache-Control": "no-store"}
            ) from None
        return respond(request, result)

    @app.get("/v3/reporting/cash-flow/events")
    def events(
        request: Request,
        prime: str,
        month: str = Query(pattern=r"^2026-(0[1-9]|1[0-2])$"),
        revision: str = Query(pattern=r"^[a-f0-9]{64}$"),
        line: str | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=1000),
        r=Depends(get_reader),  # noqa: B008
    ):
        scope(prime, int(month[:4]))
        if line and line not in {r["id"] for r in layout()} | {
            "unclassified",
            "internal-routing",
            "excluded-immaterial",
        }:
            raise HTTPException(404, "Unknown cash-flow line")
        try:
            result = r.cash_flow_events(prime, month, revision, line, offset, limit)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except Exception:
            raise HTTPException(
                503, "Cash-flow evidence store unavailable", headers={"Cache-Control": "no-store"}
            ) from None
        return respond(request, result)
