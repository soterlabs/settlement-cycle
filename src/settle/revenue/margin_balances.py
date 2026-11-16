"""Published monthly averages for the operational allocation book, not total NAV.

Daily EOD stocks use the same position/ownership adapters as the reporting B/S.
Non-invested treasury tokens and accounting receivables are excluded: NRM/NM
are returns on allocated assets, not returns on equity or total accounting NAV.
"""

import calendar
import re
from datetime import date, timedelta
from decimal import Decimal, localcontext

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .verification import canonical, digest

POLICY = "spark-margin-balances.daily-eod.v1"
DDL = """CREATE TABLE IF NOT EXISTS reporting_margin_balances (
 revision_id text PRIMARY KEY, publication_order bigserial UNIQUE,
 prime text NOT NULL, month text NOT NULL, document jsonb NOT NULL,
 document_hash text NOT NULL, code_version text NOT NULL,
 computed_at timestamptz NOT NULL DEFAULT now());
 CREATE INDEX IF NOT EXISTS reporting_margin_balances_month
 ON reporting_margin_balances(prime,month,publication_order DESC);
 CREATE TABLE IF NOT EXISTS reporting_margin_balance_evidence (
 evidence_hash text PRIMARY KEY, cutoff date NOT NULL, document jsonb NOT NULL);"""


def number(value):
    if not isinstance(value, str):
        raise ValueError("A decimal string is required")
    d = Decimal(value)
    if not d.is_finite() or d < 0:
        raise ValueError("Invalid margin balance")
    return d


def days(month):
    if not re.fullmatch(r"2026-(0[1-9]|1[0-2])", month):
        raise ValueError("Spark 2026 month required")
    start = date.fromisoformat(month + "-01")
    return [
        str(start + timedelta(days=i)) for i in range(calendar.monthrange(2026, start.month)[1])
    ]


def build(month, daily, facility, *, source_refs, universe):
    """Require every day and every declared component; never fill missing with zero."""
    expected = days(month)
    if [r["date"] for r in daily] != expected:
        raise ValueError("Complete ordered daily balance coverage required")
    if not source_refs or any(not re.fullmatch(r"[a-f0-9]{64}", v) for v in source_refs.values()):
        raise ValueError("Immutable source references required")
    required = {k: set(universe[k]) for k in ("assets", "funding")}
    if not required["assets"] or not {"sky_debt"}.issubset(required["funding"]):
        raise ValueError("Empty balance perimeter")
    if "anchorage" in required["assets"]:
        raise ValueError("Facility must not also appear in daily assets")
    with localcontext() as ctx:
        ctx.prec = 100
        averages = {family: {key: Decimal(0) for key in keys} for family, keys in required.items()}
        for row in daily:
            if not re.fullmatch(r"[a-f0-9]{64}", row["evidence_hash"]):
                raise ValueError("Daily evidence identity required")
            for family, keys in required.items():
                if set(row[family]) != keys:
                    raise ValueError("Daily component perimeter differs")
                for key, value in row[family].items():
                    averages[family][key] += number(value)
        for items in averages.values():
            for key in items:
                items[key] /= len(expected)
        averages["assets"]["anchorage"] = number(facility)
        doc = dict(
            schema_version=POLICY,
            prime="spark",
            month=month,
            period={"start": expected[0], "end": expected[-1]},
            basis="daily_EOD_arithmetic_mean_plus_published_daily_facility_principal_mean",
            scope="operational_allocation_book_including_idle_excluding_Sky_owned_legs_and_uninvested_treasury",
            averages={
                k: {name: str(v) for name, v in sorted(items.items())}
                for k, items in averages.items()
            },
            average_allocated_assets=str(sum(averages["assets"].values(), Decimal(0))),
            average_funding=str(sum(averages["funding"].values(), Decimal(0))),
            daily=daily,
            facility_average=facility,
            source_refs=source_refs,
            universe={k: sorted(v) for k, v in required.items()},
            limitations=[
                "Management returns on allocated assets; not returns on equity or total balance-sheet assets.",
                "Reporting recognition and provisional expenses are unchanged; these are not pure contractual interest margins.",
                "Anchorage uses published outstanding facility principal, never the pass-through escrow balance.",
                "Funding uses actual Vat Art times rate and gross claims for the six Savings venues included in reporting funding expense; extra B/S-only Savings vaults are excluded from this funding-rate denominator.",
                "No reconciliation residual is redistributed and no MSC settlement is changed.",
            ],
        )
    return doc


def validate(doc):
    if doc.get("schema_version") != POLICY or doc.get("prime") != "spark":
        raise ValueError("Margin balance policy differs")
    rebuilt = build(
        doc["month"],
        doc["daily"],
        doc["facility_average"],
        source_refs=doc["source_refs"],
        universe=doc["universe"],
    )
    if rebuilt != doc:
        raise ValueError("Margin balance arithmetic or scope differs")
    return doc


def validate_evidence(document, evidence):
    from .margin_balance_job import daily_row

    if set(evidence) != {r["date"] for r in document["daily"]}:
        raise ValueError("Daily raw evidence coverage differs")
    for row in document["daily"]:
        raw = evidence[row["date"]]
        if daily_row(raw["stocks"], raw["supplement"]) != row:
            raise ValueError("Published balances differ from raw evidence")


def publish(conn, document, code, evidence):
    validate(document)
    validate_evidence(document, evidence)
    conn.execute(DDL)
    for row in document["daily"]:
        conn.execute(
            "INSERT INTO reporting_margin_balance_evidence(evidence_hash,cutoff,document) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
            (row["evidence_hash"], row["date"], Jsonb(evidence[row["date"]])),
        )
    hashed = digest(document)
    revision = digest({"document_hash": hashed, "code_version": code})
    conn.execute(
        "INSERT INTO reporting_margin_balances(revision_id,prime,month,document,document_hash,code_version) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (revision, "spark", document["month"], Jsonb(document), hashed, code),
    )
    return revision


def read(conn, month, revision=None):
    if not conn.execute("SELECT to_regclass('reporting_margin_balances')").fetchone()[0]:
        return None
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM reporting_margin_balances WHERE prime='spark' AND month=%s"
            + (" AND revision_id=%s" if revision else "")
            + " ORDER BY publication_order DESC LIMIT 1",
            (month, revision) if revision else (month,),
        )
        row = cur.fetchone()
    if row:
        row = canonical(row)
        validate(row["document"])
        if (
            row["document"]["month"] != month
            or digest(row["document"]) != row["document_hash"]
            or row["revision_id"]
            != digest({"document_hash": row["document_hash"], "code_version": row["code_version"]})
        ):
            raise ValueError("Margin balance publication identity differs")
    return row
