"""Append-only, reporting-only closing evidence. Never writes MSC tables."""

import re
from datetime import UTC, date, datetime, time
from decimal import Decimal, localcontext

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .verification import canonical, digest

DDL = """CREATE TABLE IF NOT EXISTS reporting_balance_sheet_snapshots (
 revision_id text PRIMARY KEY, publication_order bigserial UNIQUE,
 prime text NOT NULL, cutoff date NOT NULL, document jsonb NOT NULL,
 document_hash text NOT NULL, code_version text NOT NULL,
 computed_at timestamptz NOT NULL DEFAULT now());
 CREATE INDEX IF NOT EXISTS reporting_balance_sheet_snapshot_dates
 ON reporting_balance_sheet_snapshots(prime,cutoff,publication_order DESC);"""


def validate(document):
    if (
        document.get("schema_version") != "spark-closing-stocks.v1"
        or document.get("prime") != "spark"
    ):
        raise ValueError("Stock scope differs")
    if document.get("proof_hash") != digest(
        {k: v for k, v in document.items() if k != "proof_hash"}
    ):
        raise ValueError("Stock evidence hash differs")
    cutoff = date.fromisoformat(document["cutoff"])
    if cutoff.year != 2026:
        raise ValueError("Stock year differs")
    target = int(datetime.combine(cutoff, time.max, UTC).timestamp())
    for pin in document["pins"].values():
        if (
            not isinstance(pin["block"], int)
            or pin["block"] < 0
            or not re.fullmatch(r"0x[0-9a-fA-F]{64}", pin["hash"])
        ):
            raise ValueError("Invalid chain pin")
        if (
            not pin.get("before_first_block")
            and not pin["timestamp"] <= target < pin["successor_timestamp"]
        ):
            raise ValueError("Closing boundary differs")
        if pin.get("before_first_block") and (
            pin["block"] != 0 or pin["successor_timestamp"] <= target
        ):
            raise ValueError("Invalid pre-genesis boundary")
    for read in document["reads"]:
        if (
            read["chain"] not in document["pins"]
            or read["method"] not in {"eth_call", "eth_getCode", "eth_getBalance"}
            or not isinstance(read["result"], str)
            or not re.fullmatch(r"0x[0-9a-fA-F]*", read["result"])
        ):
            raise ValueError("Invalid raw evidence")
    for family in ["positions", "liabilities", "extra_assets", "debt"]:
        for row in (
            {"sky_debt": document["sky_debt"]} if family == "debt" else document[family]
        ).values():
            if row["status"] == "available":
                value = Decimal(row["value"])
                if not value.is_finite() or value < 0:
                    raise ValueError("Invalid closing stock")
                if row.get("quality") not in {"supported", "estimate"}:
                    raise ValueError("Invalid stock quality")
                if any(
                    not isinstance(i, int) or i < 0 or i >= len(document["reads"])
                    for i in row.get("read_indices", [])
                ):
                    raise ValueError("Invalid evidence reference")
            elif row["status"] != "unavailable" or row["value"] is not None:
                raise ValueError("Stock availability differs")
    for row in document["ownership"].values():
        with localcontext() as ctx:
            ctx.prec = 100
            gross, sky, prime = (
                Decimal(row[k]) for k in ["gross_value", "sky_value", "prime_value"]
            )
            if (
                not all(v.is_finite() for v in (gross, sky, prime))
                or min(gross, sky, prime) < 0
                or abs(gross - sky - prime) > Decimal("1e-70")
            ):
                raise ValueError("Stock ownership does not reconcile")
    return document


def publish(conn, document, code):
    validate(document)
    conn.execute(DDL)
    hashed = digest(document)
    revision = digest({"document_hash": hashed, "code_version": code})
    conn.execute(
        "INSERT INTO reporting_balance_sheet_snapshots(revision_id,prime,cutoff,document,document_hash,code_version) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (revision, "spark", document["cutoff"], Jsonb(document), hashed, code),
    )
    return revision


def year_rows(conn, year, revisions=None, as_of=None):
    if not conn.execute("SELECT to_regclass('reporting_balance_sheet_snapshots')").fetchone()[0]:
        return []
    params = ["spark", f"{year}-01-01", str(as_of) if as_of else f"{year}-12-31"]
    clause = ""
    if revisions is not None:
        clause = " AND revision_id=ANY(%s)"
        params.append(revisions)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT DISTINCT ON(cutoff) * FROM reporting_balance_sheet_snapshots WHERE prime=%s AND cutoff BETWEEN %s AND %s"
            + clause
            + " ORDER BY cutoff,publication_order DESC",
            params,
        )
        rows = canonical(cur.fetchall())
    for row in rows:
        if (
            digest(row["document"]) != row["document_hash"]
            or row["cutoff"] != row["document"]["cutoff"]
        ):
            raise ValueError("Stock publication differs")
        if row["revision_id"] != digest(
            {"document_hash": row["document_hash"], "code_version": row["code_version"]}
        ):
            raise ValueError("Stock revision identity differs")
        validate(row["document"])
    return rows
