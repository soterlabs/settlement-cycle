"""Append-only reporting components, isolated from MSC and native revenue."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, localcontext

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .financial import number
from .verification import canonical, digest


def validate_document(document):
    if (
        document.get("schema_version") != "spark-onchain.v1"
        or document["period_start"] != document["cutoff"][:7] + "-01"
    ):
        raise ValueError("Invalid Spark component identity")
    date.fromisoformat(document["cutoff"])
    with localcontext() as ctx:
        ctx.prec = 60
        for family in ("savings_interest", "statement_allocations", "native_supplies"):
            for row in document.get(family, {}).values():
                proof = row.get("absence_proof")
                if proof and (
                    proof.get("opening_code") != "0x"
                    or proof.get("closing_code") != "0x"
                    or proof.get("event_count") != 0
                    or number(row["value"]) != 0
                ):
                    raise ValueError("Invalid undeployed-contract zero proof")
        for program, row in document.get("issuer_income", {}).items():
            if program not in ("rlusd", "pyusd", "usdg"):
                raise ValueError("Unknown monthly issuer program")
            if row.get("status") == "available":
                value = number(row["value"])
                if row.get("program") != program:
                    raise ValueError("Monthly issuer program identity differs")
                if program == "usdg" and value != number(row.get("period_receipts_usd", "0")):
                    raise ValueError("USDG income differs from verified receipts")
                if program != "usdg" and row.get("basis") != "issuer_program_not_started":
                    days = row.get("daily", [])
                    first, last = date.fromisoformat(document["period_start"]), date.fromisoformat(document["cutoff"])
                    expected = [str(first + timedelta(days=i)) for i in range((last - first).days + 1)]
                    if [d["date"] for d in days] != expected:
                        raise ValueError("Monthly issuer daily coverage is incomplete")
                    if sum((number(d["accrual"]) for d in days), Decimal(0)) != value:
                        raise ValueError("Monthly issuer accrual does not reconcile")
                    if any(sum((number(c["accrual"]) for c in d["components"]), Decimal(0)) != number(d["accrual"]) for d in days):
                        raise ValueError("Monthly issuer eligible components do not reconcile")
        for row in document.get("curator_fees", {}).values():
            if row.get("status") in ("available", "partial"):
                opening, earned, paid, closing = (
                    number(row[key])
                    for key in (
                        "opening_pending_usd",
                        "accrued_usd",
                        "received_usd",
                        "closing_pending_usd",
                    )
                )
                if min(opening, paid, closing) < 0 or opening + earned - paid != closing:
                    raise ValueError("Unreconciled curator fee ledger")
        from .spark_savings import VAULTS

        expected_assets = dict(
            zip(VAULTS, ("USDC", "USDT", "WETH", "PYUSD", "USDC", "USDG"), strict=True)
        )
        for venue, row in document.get("savings_interest", {}).items():
            if venue not in expected_assets or row.get("venue") != venue:
                raise ValueError("Unexpected Savings identity")
            if row.get("status") in ("available", "partial"):
                if row.get("asset") != expected_assets[venue]:
                    raise ValueError("Savings underlying differs from reviewed identity")
                opening, closing, deposits, withdrawals, interest, rounding = (
                    number(row[key])
                    for key in (
                        "opening_claim_units",
                        "closing_claim_units",
                        "deposits_units",
                        "withdrawals_units",
                        "interest_units",
                        "share_rounding_units",
                    )
                )
                if (
                    min(opening, closing, deposits, withdrawals) < 0
                    or closing - opening - deposits + withdrawals != interest + rounding
                ):
                    raise ValueError("Unreconciled Savings capital/interest bridge")
                number(row["value"])
                if row["asset"] != "WETH" and number(row["value"]) != interest:
                    raise ValueError("Stablecoin interest must be valued at par")
                if row["asset"] == "WETH" and not row.get("absence_proof"):

                    prices = row["daily_translation"]
                    start = date.fromisoformat(document["period_start"])
                    end = date.fromisoformat(row.get("period_end", document["cutoff"]))
                    expected_days = [
                        str(start + timedelta(days=i)) for i in range((end - start).days + 1)
                    ]
                    if [p["date"] for p in prices] != expected_days or any(
                        number(p["usd_price"]) <= 0 for p in prices
                    ):
                        raise ValueError("ETH Savings translation days are incomplete or invalid")
                    units = sum((number(p["interest_units"]) for p in prices), Decimal(0))
                    usd = sum(
                        (number(p["interest_units"]) * number(p["usd_price"]) for p in prices),
                        Decimal(0),
                    )
                    if units != interest or usd != number(row["value"]):
                        raise ValueError("ETH Savings translation does not reconcile")
        for family in ("native_supplies", "statement_allocations"):
            for row in document.get(family, {}).values():
                proof = row.get("reconciliation")
                if proof and row.get("status") == "available":
                    opening, closing, capital, interest = (
                        number(proof[k])
                        for k in (
                            "opening_claim_units",
                            "closing_claim_units",
                            "net_capital_units",
                            "interest_units",
                        )
                    )
                    if min(opening, closing) < 0 or closing - opening - capital != interest:
                        raise ValueError("Unreconciled supply capital/interest bridge")
                    if row.get("translation_policy"):

                        start = date.fromisoformat(document["period_start"])
                        end = date.fromisoformat(document["cutoff"])
                        prices = row["daily_translation"]
                        if [p["date"] for p in prices] != [
                            str(start + timedelta(days=i)) for i in range((end - start).days + 1)
                        ]:
                            raise ValueError("Incomplete supply translation days")
                        if any(number(p["usd_price"]) <= 0 for p in prices):
                            raise ValueError("Invalid supply translation price")
                        if sum(
                            (number(p["interest_units"]) for p in prices), Decimal(0)
                        ) != interest or sum(
                            (number(p["interest_units"]) * number(p["usd_price"]) for p in prices),
                            Decimal(0),
                        ) != number(row["value"]):
                            raise ValueError("Unreconciled supply daily translation")
                    elif number(row["value"]) != interest:
                        raise ValueError("Supply revenue differs from reconciled interest")
        for row in document.get("amm_fees", {}).values():
            proof = row.get("capital_reconciliation", {})
            if proof.get("status") == "available" and number(
                proof["closing_position_usd"]
            ) - number(proof["opening_position_usd"]) - number(
                proof["net_capital_added_usd"]
            ) + number(row["value"]) != number(proof["gross_return_usd"]):
                raise ValueError("Unreconciled AMM capital/return bridge")
        for row in document.get("amm_fees", {}).values():
            if row.get("status") in ("available", "partial") and (
                number(row["collected_usd"])
                + number(row["closing_pending_usd"])
                - number(row["opening_pending_usd"])
                != number(row["value"])
            ):
                raise ValueError("Unreconciled AMM fee accrual")
        for row in document.get("gains", {}).values():
            if row.get("status") in ("available", "partial"):
                net = number(row["value"])
                if row.get("daily_revaluation"):

                    start = date.fromisoformat(document["period_start"])
                    end = date.fromisoformat(document["cutoff"])
                    entries = row["daily_revaluation"]
                    if [p["date"] for p in entries] != [
                        str(start + timedelta(days=i)) for i in range((end - start).days + 1)
                    ]:
                        raise ValueError("Net revaluation dates do not cover the period")
                    if [p["asset_bridge"] for p in entries] != document.get(
                        "statement_allocations", {}
                    ).get("sparklend.weth", {}).get("daily_translation") or [
                        p["liability_bridge"] for p in entries
                    ] != document.get("savings_interest", {}).get("S58", {}).get("daily_liability"):
                        raise ValueError("Net revaluation does not use the saved source claims")
                    previous = {}
                    previous_price = None
                    for entry in entries:
                        opening_price, closing_price = (
                            number(entry["opening_usd_price"]),
                            number(entry["closing_usd_price"]),
                        )
                        if min(opening_price, closing_price) <= 0 or (
                            previous_price is not None and opening_price != previous_price
                        ):
                            raise ValueError("Net revaluation closing quotes do not reconcile")
                        if number(entry["asset_price_gain_usd"]) != number(
                            entry["asset_bridge"]["opening_claim_units"]
                        ) * (closing_price - opening_price) or number(
                            entry["depositor_offset_usd"]
                        ) != number(entry["liability_bridge"]["opening_claim_units"]) * (
                            closing_price - opening_price
                        ):
                            raise ValueError(
                                "Net revaluation gain differs from the daily closing convention"
                            )
                        previous_price = closing_price
                        for family in ("asset_bridge", "liability_bridge"):
                            part = entry[family]
                            opening, closing, capital, earned = (
                                number(part[k])
                                for k in (
                                    "opening_claim_units",
                                    "closing_claim_units",
                                    "net_capital_units",
                                    "interest_units",
                                )
                            )
                            if closing - opening - capital != earned or (
                                family in previous and previous[family] != opening
                            ):
                                raise ValueError(
                                    "Net revaluation daily claim bridges do not reconcile"
                                )
                            previous[family] = closing
                    if sum(
                        (number(p["asset_price_gain_usd"]) for p in entries), Decimal(0)
                    ) != number(row["asset_price_gain_usd"]) or sum(
                        (number(p["depositor_offset_usd"]) for p in entries), Decimal(0)
                    ) != number(row["depositor_offset_usd"]):
                        raise ValueError("Net revaluation daily gains do not reconcile")
                if "asset_price_gain_usd" in row and abs(
                    number(row["asset_price_gain_usd"]) - number(row["depositor_offset_usd"]) - net
                ) > Decimal("1e-36"):
                    raise ValueError("Net gain does not offset depositor liabilities")


def publish(conn, document, *, code_version, configuration_version):
    if (
        document.get("schema_version") != "spark-onchain.v1"
        or document["period_start"] != document["cutoff"][:7] + "-01"
    ):
        raise ValueError("Invalid Spark reporting component period")
    validate_document(document)
    if not code_version or not configuration_version:
        raise ValueError("Missing Spark reporting component version")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT * FROM reporting_spark_components WHERE cutoff BETWEEN %s AND %s
            ORDER BY cutoff DESC,publication_order DESC LIMIT 1""",
            (document["period_start"], document["cutoff"]),
        )
        previous = cur.fetchone()
    if previous:
        document = carry_forward(document, previous)
    validate_document(document)
    identity = {
        "document_hash": digest(document),
        "code_version": code_version,
        "configuration_version": configuration_version,
    }
    revision = digest(identity)
    conn.execute(
        """INSERT INTO reporting_spark_components
        (revision_id,cutoff,document,document_hash,code_version,configuration_version)
        VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
        (
            revision,
            document["cutoff"],
            Jsonb(document),
            identity["document_hash"],
            code_version,
            configuration_version,
        ),
    )
    return revision


def carry_forward(document, previous):
    """Retain known same-month amounts after a failed refresh, with old bounds."""
    result = deepcopy(document)
    if previous["document"]["period_start"] != document["period_start"]:
        return result

    def retained(new, old):
        if new.get("status") != "unavailable" or old.get("status") not in ("available", "partial"):
            return new
        return {
            **old,
            "status": "partial",
            "reason": "retained_after_refresh_failure",
            "period_end": old.get("period_end", str(previous["cutoff"])),
            "retained_source_revision": old.get(
                "retained_source_revision", previous["revision_id"]
            ),
        }

    result["reserve_fees"] = retained(
        result.get("reserve_fees", {}), previous["document"].get("reserve_fees", {})
    )
    for family in (
        "curator_fees",
        "gains",
        "native_supplies",
        "savings_interest",
        "statement_allocations",
        "amm_fees",
        "issuer_income",
    ):
        for key, value in result.get(family, {}).items():
            result[family][key] = retained(value, previous["document"].get(family, {}).get(key, {}))
    return result


def year_rows(conn, year, *, revisions=None):
    with conn.cursor(row_factory=dict_row) as cur:
        if revisions is not None:
            cur.execute(
                "SELECT * FROM reporting_spark_components WHERE revision_id = ANY(%s)", (revisions,)
            )
        else:
            cur.execute(
                """SELECT DISTINCT ON (cutoff) * FROM reporting_spark_components
                WHERE cutoff BETWEEN %s AND %s ORDER BY cutoff,publication_order DESC""",
                (f"{year}-01-01", f"{year}-12-31"),
            )
        rows = canonical(cur.fetchall())
    for row in rows:
        if digest(row["document"]) != row["document_hash"]:
            raise ValueError("Spark reporting component hash mismatch")
        validate_document(row["document"])
        row["source"] = {
            "kind": "spark_onchain_components",
            "revision_id": row["revision_id"],
            "cutoff": row["cutoff"],
            "result_hash": row["document_hash"],
            "currency": "USD",
            "code_version": row["code_version"],
            "configuration_version": row["configuration_version"],
            "opening_pins": row["document"].get("opening_pins", {}),
            "closing_pins": row["document"].get("closing_pins", {}),
        }
    return rows


def select(rows, year, as_of, selection=None):
    chosen = []
    for month in range(1, 13):
        key = f"{year}-{month:02}"
        if selection is not None:
            ref = selection["offchain_revisions"][month - 1]
            match = next((row for row in rows if row["revision_id"] == ref), None) if ref else None
            if ref and (not match or match["cutoff"][:7] != key or match["cutoff"] > str(as_of)):
                raise ValueError("Retained Spark component revision unavailable")
        else:
            matches = [
                row for row in rows if row["cutoff"][:7] == key and row["cutoff"] <= str(as_of)
            ]
            match = max(
                matches, key=lambda row: (row["cutoff"], row["publication_order"]), default=None
            )
        chosen.append(match)
    return chosen
