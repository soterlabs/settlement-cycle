"""Append-only cash-flow publications/evidence, isolated from settlement stores."""

from datetime import date

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..verification import canonical, digest
from .model import validate

DDL = """CREATE TABLE IF NOT EXISTS reporting_cash_flow_publications (
 revision_id text PRIMARY KEY, publication_order bigint GENERATED ALWAYS AS IDENTITY,
 prime text NOT NULL, cutoff date NOT NULL, policy_version text NOT NULL,
 computed_at timestamptz NOT NULL DEFAULT now(), document jsonb NOT NULL,
 document_hash text NOT NULL, evidence jsonb NOT NULL, evidence_hash text NOT NULL,
 code_version text NOT NULL, configuration_revision text NOT NULL);
 CREATE INDEX IF NOT EXISTS reporting_cash_flow_latest ON reporting_cash_flow_publications(prime,cutoff,publication_order DESC);
 CREATE TABLE IF NOT EXISTS reporting_cash_flow_capture_cache (
 configuration_revision text NOT NULL, chain text NOT NULL, cutoff date NOT NULL,
 document jsonb NOT NULL, document_hash text NOT NULL,
 PRIMARY KEY(configuration_revision,chain,cutoff));"""


def apply_schema(conn):
    conn.execute(DDL)


def publish(conn, document, events, code_version):
    validate(document)
    if (
        digest(events) != document["cash_event_digest"]
        or len(events) != document["cash_event_count"]
    ):
        raise ValueError("Cash-flow evidence digest differs")
    hashed = digest(document)
    event_hash = digest(events)
    revision = digest(
        {
            "document_hash": hashed,
            "evidence_hash": event_hash,
            "code_version": code_version,
            "policy_version": document["policy_version"],
        }
    )
    conn.execute(
        """INSERT INTO reporting_cash_flow_publications (revision_id,prime,cutoff,policy_version,document,document_hash,evidence,evidence_hash,code_version,configuration_revision)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
        (
            revision,
            document["prime"],
            document["cutoff"],
            document["policy_version"],
            Jsonb(document),
            hashed,
            Jsonb(events),
            event_hash,
            code_version,
            document["configuration_revision"],
        ),
    )
    saved = conn.execute(
        "SELECT document_hash,evidence_hash FROM reporting_cash_flow_publications WHERE revision_id=%s",
        (revision,),
    ).fetchone()
    if tuple(saved) != (hashed, event_hash):
        raise ValueError("Cash-flow publication identity differs")
    return revision


def year_rows(conn, prime, year, revisions=None, as_of=None):
    distinct = "cutoff" if revisions is not None else "date_trunc('month',cutoff)"
    query = f"""SELECT DISTINCT ON({distinct}) revision_id,publication_order,prime,cutoff,policy_version,computed_at,document,document_hash,evidence_hash,code_version,configuration_revision
    FROM reporting_cash_flow_publications WHERE prime=%s AND cutoff BETWEEN %s AND %s"""
    params = [
        prime,
        date(year, 1, 1),
        min(as_of, date(year, 12, 31)) if as_of else date(year, 12, 31),
    ]
    if revisions is not None:
        query += " AND revision_id=ANY(%s)"
        params.append(revisions)
    query += " ORDER BY " + distinct + ",cutoff DESC,publication_order DESC"
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        rows = canonical(cur.fetchall())
    for row in rows:
        if (
            digest(row["document"]) != row["document_hash"]
            or row["document"]["cutoff"] != row["cutoff"]
            or row["document"]["prime"] != prime
        ):
            raise ValueError("Cash-flow publication hash/scope differs")
        validate(row["document"])
    return rows


def events(conn, prime, month, revision, line, offset, limit):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT cutoff,document_hash,evidence_hash FROM reporting_cash_flow_publications WHERE prime=%s AND revision_id=%s",
            (prime, revision),
        )
        source = cur.fetchone()
        if not source or str(source["cutoff"])[:7] != month:
            raise LookupError("Cash-flow evidence selection not found")
        params = [prime, revision]
        where = "WHERE p.prime=%s AND p.revision_id=%s"
        if line is not None:
            where += " AND e.value->>'line_id'=%s"
            params.append(line)
        base = (
            "FROM reporting_cash_flow_publications p CROSS JOIN LATERAL jsonb_array_elements(p.evidence) WITH ORDINALITY AS e(value,n) "
            + where
        )
        cur.execute("SELECT count(*) AS count " + base, params)
        count = cur.fetchone()["count"]
        cur.execute(
            "SELECT e.value " + base + " ORDER BY e.n OFFSET %s LIMIT %s", [*params, offset, limit]
        )
        values = [r["value"] for r in cur.fetchall()]
    return {
        "schema_version": "cash-flow-events.v1",
        "prime": prime,
        "month": month,
        "revision": revision,
        "document_hash": source["document_hash"],
        "evidence_hash": source["evidence_hash"],
        "line_id": line,
        "total_count": count,
        "offset": offset,
        "limit": limit,
        "events": values,
        "next_offset": offset + len(values) if offset + len(values) < count else None,
    }


def balances(conn, prime, month, revision):
    rows = year_rows(conn, prime, int(month[:4]), [revision])
    if len(rows) != 1 or rows[0]["cutoff"][:7] != month:
        raise LookupError("Cash-flow balance selection not found")
    row = rows[0]
    return {
        "schema_version": "cash-flow-balances.v1",
        "prime": prime,
        "month": month,
        "revision": revision,
        "document_hash": row["document_hash"],
        "balance_witnesses": row["document"].get("balance_witnesses", {}),
        "coverage": row["document"]["coverage"],
    }
