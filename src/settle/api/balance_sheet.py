"""Read-only closing stocks. No RPC, replay or settlement writes on HTTP reads."""

from datetime import date, timedelta

from fastapi import Depends, HTTPException, Query, Request
from psycopg.rows import dict_row

from ..revenue import balance_sheet as projection
from ..revenue import balance_sheet_store, spark_reporting_store
from ..revenue.cash_flow import store as cash_store
from ..revenue.verification import canonical
from . import revenue


def inputs(conn, year, as_of, selection=None):
    def refs(kind):
        return (
            [m.get(kind) for m in selection["months"] if m and m.get(kind)] if selection else None
        )

    def where(kind):
        selected = refs(kind)
        if selected is not None:
            return "revision_id = ANY(%s)", (selected,)
        return (
            "cutoff BETWEEN %s AND %s AND (cutoff = (date_trunc('month', cutoff) + interval '1 month - 1 day')::date OR date_trunc('month', cutoff) = date_trunc('month', %s::date))",
            (f"{year}-01-01", str(as_of), str(as_of)),
        )

    with conn.cursor(row_factory=dict_row) as cur:
        clause, params = where("venues")
        cur.execute(
            f"""SELECT DISTINCT ON(cutoff) revision_id, publication_order, prime, cutoff, result_hash,
                    COALESCE(result->'venue_views'->'reporting', '[]'::jsonb) AS venues
                    FROM revenue_results WHERE prime='spark' AND {clause}
                    ORDER BY cutoff, publication_order DESC""",
            params,
        )
        venues = canonical(cur.fetchall())
        selected_ids = {}
        for kind, table in [
            ("cash", "reporting_cash_flow_publications"),
            ("components", "reporting_spark_components"),
        ]:
            clause, params = where(kind)
            policy = (
                " AND prime='spark' AND policy_version='spark-cash-flow.events.v2'"
                if kind == "cash"
                else ""
            )
            cur.execute(
                f"SELECT DISTINCT ON(cutoff) revision_id FROM {table} WHERE {clause}{policy} ORDER BY cutoff, publication_order DESC",
                params,
            )
            selected_ids[kind] = [r["revision_id"] for r in cur.fetchall()]
    return dict(
        venues=venues,
        stocks=balance_sheet_store.year_rows(
            conn, year, refs("stocks") if selection else None, as_of=as_of
        ),
        cash=cash_store.year_rows(conn, "spark", year, selected_ids["cash"], as_of=as_of),
        components=spark_reporting_store.year_rows(
            conn, year, revisions=selected_ids["components"]
        ),
    )


class BalanceSheetReaderMixin:
    def balance_sheet_inputs(self, year, as_of, selection=None):
        with self._pool.connection() as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                return inputs(conn, year, as_of, selection)


def register(app, get_reader, document_response):
    @app.get("/v3/reporting/balance-sheet")
    def balance_sheet(
        request: Request,
        prime: str,
        year: int = Query(ge=2020, le=9998),
        cutoff: date | None = None,
        revision: str | None = Query(None, max_length=8192),
        r=Depends(get_reader),  # noqa: B008
    ):
        if prime != "spark" or year != 2026:
            raise HTTPException(404, "Balance sheet policy currently supports Spark 2026")
        if cutoff and revision:
            raise HTTPException(422, "Select a cutoff or an immutable selection, not both")
        selected = None
        if revision:
            try:
                selected = projection.decode(revision, prime, year)
                cutoff = date.fromisoformat(selected["as_of"])
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        as_of = cutoff or min(revenue.today() - timedelta(days=1), date(year, 12, 31))
        if as_of.year != year or as_of >= revenue.today():
            raise HTTPException(422, "Cutoff must be a completed UTC day in the selected year")
        try:
            data = r.balance_sheet_inputs(year, as_of, selected)
            result = projection.annual(year, as_of, data, selection=selected)
        except (ValueError, LookupError) as exc:
            raise HTTPException(404, str(exc), headers={"Cache-Control": "no-store"}) from None
        except Exception:
            raise HTTPException(
                503,
                "Balance sheet reporting store unavailable",
                headers={"Cache-Control": "no-store"},
            ) from None
        return document_response(request, result, max_age=30)
