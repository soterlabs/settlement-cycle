"""Envio-backed ``IDebtSource`` — GraphQL over a self-hosted HyperIndex.

Drop-in replacement for :class:`~settle.normalize.sources.dune_debt.DuneDebtSource`.
It returns the **identical** contract (see ``debt_timeseries`` below) so the
Normalize/Compute layers can't tell which source produced the frame — the whole
point of the ``IDebtSource`` protocol. This lets us run Dune and Envio
side-by-side (``scripts/compare_debt_sources.py``) and only retire the Dune
query once the two agree.

Data contract (must match ``debt_timeseries.sql`` byte-for-byte):

    columns: [block_date (date), daily_dart (Decimal), cum_debt (Decimal)]

    * cum_debt is NORMALISED Art (Σ dart, wad units / 1e18) — NOT rate-scaled
      USDS. The rate index is applied downstream in ``normalize/debt.py`` via
      an RPC read of ``Vat.ilks(ilk).rate``. Envio must NOT pre-apply it.
    * dart is the signed int256 pulled from the ``frob``/``grab`` calldata at
      offset 165 (draws positive, repays negative). Summed per ``block_date``,
      then a running cumulative sum ordered by date.

Indexer contract (what the HyperIndex must expose — see docs/envio/README.md):

    A GraphQL entity, default name ``VatDebtEvent``, one row per frob/grab
    contribution to ``urns[ilk][u].art`` on the Vat, with fields:

        ilk            String   -- 0x-prefixed 32-byte hex, lower-case
        dart           String   -- raw signed int256 in WAD (1e18), NOT /1e18
        blockNumber    Int
        blockTimestamp Int      -- unix seconds, UTC

    The Vat's ``note`` modifier emits an anonymous ``LogNote`` log on every
    frob/grab, so these are indexable as ordinary events (no trace support
    needed). See the README for the exact selector/offset decode.

Configuration (env):

    ENVIO_GRAPHQL_URL      required — e.g. http://localhost:8080/v1/graphql
    ENVIO_GRAPHQL_TOKEN    optional — sent as ``Authorization: Bearer <token>``
                           (Hasura admin secret goes via x-hasura-admin-secret
                           instead; set ENVIO_HASURA_ADMIN_SECRET for that.)
    ENVIO_DEBT_ENTITY      optional — override the entity name (default
                           ``VatDebtEvent``).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd
import requests

_WAD = Decimal(10) ** 18
_PAGE_SIZE = 1000
_DEFAULT_TIMEOUT = 30
_DEFAULT_ENTITY = "VatDebtEvent"


class EnvioError(RuntimeError):
    """Raised on Envio/GraphQL transport or query errors."""


def _endpoint() -> str:
    url = os.environ.get("ENVIO_GRAPHQL_URL")
    if not url:
        raise EnvioError(
            "Missing env var ENVIO_GRAPHQL_URL (Envio HyperIndex GraphQL endpoint, "
            "e.g. http://localhost:8080/v1/graphql)"
        )
    return url


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if secret := os.environ.get("ENVIO_HASURA_ADMIN_SECRET"):
        h["x-hasura-admin-secret"] = secret
    if token := os.environ.get("ENVIO_GRAPHQL_TOKEN"):
        h["Authorization"] = f"Bearer {token}"
    return h


def _entity() -> str:
    return os.environ.get("ENVIO_DEBT_ENTITY", _DEFAULT_ENTITY)


class EnvioDebtSource:
    """Implements ``IDebtSource`` against an Envio HyperIndex GraphQL endpoint.

    Injectable ``post`` for tests: any callable ``(url, json, headers, timeout)
    -> requests.Response``-like object with ``.ok``, ``.status_code``,
    ``.text`` and ``.json()``. Defaults to :func:`requests.post`.
    """

    def __init__(self, post: Callable[..., Any] = requests.post) -> None:
        self._post = post

    def debt_timeseries(self, ilk: bytes, start: date, pin_block: int) -> pd.DataFrame:
        rows = self._fetch_events(ilk, start, pin_block)
        cols = ["block_date", "daily_dart", "cum_debt"]
        if not rows:
            return pd.DataFrame(columns=cols)

        # Sparse per-event → daily aggregate → cumulative, mirroring the
        # ``daily`` CTE + window in debt_timeseries.sql exactly.
        df = pd.DataFrame(rows)
        df["block_date"] = df["ts"].apply(
            lambda s: datetime.fromtimestamp(int(s), tz=timezone.utc).date()
        )
        # Sum signed dart per calendar day, carrying Decimal to keep every
        # wad byte exact (float would drop ~3 sig figs — PRD §10).
        daily = (
            df.groupby("block_date")["dart"]
            .apply(lambda s: sum(s, Decimal(0)) / _WAD)
            .reset_index(name="daily_dart")
            .sort_values("block_date")
            .reset_index(drop=True)
        )
        daily["cum_debt"] = daily["daily_dart"].cumsum()
        return daily[cols]

    # -- transport ---------------------------------------------------------

    def _fetch_events(self, ilk: bytes, start: date, pin_block: int) -> list[dict[str, Any]]:
        """Page through every frob/grab event for ``ilk`` at ``block <= pin``.

        Filters mirror the SQL WHERE clause: ilk match, ``block_number <= pin``,
        ``block_date >= start``. We filter on ``blockTimestamp`` at start-of-day
        UTC (Envio has no derived block_date column); events land on the correct
        calendar day after the UTC conversion in ``debt_timeseries``.
        """
        ilk_hex = "0x" + bytes(ilk).hex()
        start_ts = int(
            datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp()
        )
        entity = _entity()
        query = f"""
        query DebtEvents($ilk: String!, $pin: Int!, $startTs: Int!, $limit: Int!, $offset: Int!) {{
          {entity}(
            where: {{
              ilk: {{ _eq: $ilk }},
              blockNumber: {{ _lte: $pin }},
              blockTimestamp: {{ _gte: $startTs }}
            }},
            order_by: [{{ blockNumber: asc }}, {{ id: asc }}],
            limit: $limit,
            offset: $offset
          ) {{
            dart
            blockNumber
            blockTimestamp
          }}
        }}
        """
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            variables = {
                "ilk": ilk_hex,
                "pin": int(pin_block),
                "startTs": start_ts,
                "limit": _PAGE_SIZE,
                "offset": offset,
            }
            page = self._execute(query, variables)[entity]
            for r in page:
                out.append({"dart": Decimal(str(r["dart"])), "ts": r["blockTimestamp"]})
            if len(page) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
        return out

    def _execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._post(
                _endpoint(),
                json={"query": query, "variables": variables},
                headers=_headers(),
                timeout=_DEFAULT_TIMEOUT,
            )
        except requests.RequestException as exc:  # transport failure
            raise EnvioError(f"Envio GraphQL request failed: {exc}") from exc
        if not resp.ok:
            raise EnvioError(
                f"Envio GraphQL → HTTP {resp.status_code}: {resp.text[:400]}"
            )
        body = resp.json()
        if body.get("errors"):
            raise EnvioError(f"Envio GraphQL errors: {body['errors']}")
        data: dict[str, Any] = body["data"]
        return data
