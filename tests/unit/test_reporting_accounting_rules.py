from datetime import UTC, date, datetime
from decimal import Decimal

from settle.revenue.cash_flow.classify import collect_ledger
from settle.revenue.cash_flow.config import load
from settle.revenue.cash_flow.model import amount, metric, subtotals
from settle.revenue.spark_ownership import WINDOWS
from tests.unit.test_cash_flow import event

ALM = "0x1601843c5e9bc251a3272907010afa41fa18347e"
USDS = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
PSM = "0xa188eec8f81263234da3622a406892f3d630f98c"


def transfer(token, src, dst, quantity, day="2026-03-06", tx=None, index=0):
    row = event(
        10,
        "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
        quantity,
        None,
        src=src,
        dst=dst,
    )
    row.update(
        address=token,
        timestamp=int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp()),
        log_index=index,
    )
    if tx:
        row["tx_hash"] = tx
    return row


def ledger(rows, **kwargs):
    return collect_ledger({"chains": {"ethereum": {"events": rows}}}, config=load(), **kwargs)[
        "entries"
    ]


def test_issuer_rewards_do_not_override_the_cash_perimeter():
    config = load()
    tokens = {v["symbol"]: t for t, v in config["tokens"]["ethereum"].items()}
    for payer, symbol in config["reward_payers"]["ethereum"].items():
        assert ledger([transfer(tokens[symbol], payer, ALM, 10**18)]) == []


def test_psm_both_cash_legs_are_conversion_not_expense():
    rows = [transfer(USDS, ALM, PSM, 100 * 10**18), transfer(USDC, PSM, ALM, 100 * 10**6, index=1)]
    entries = ledger(rows)
    assert entries and {r["line_id"] for r in entries} == {"internal-routing"}
    assert sum(Decimal(r["amount_usd"]) for r in entries) == 0


def test_july_principal_uses_exact_msc_transaction_witness():
    row = transfer(
        USDC,
        "0x49506c3aa028693458d6ee816b2ec28522946872",
        ALM,
        10000017287194,
        "2026-07-21",
        "0x4ad39783662761407cc239326f07ebe8102485838934f03a94d1b59bec512c69",
    )
    entries = ledger([row])
    assert [(r["line_id"], r["amount_usd"]) for r in entries] == [
        ("allocation-changes", "10000017.287194")
    ]
    # Different receipts cannot inherit a date-only principal override.
    row["tx_hash"] = "0x" + "f" * 64
    assert ledger([row])[0]["line_id"] == "external-receipts"


def test_sde_is_leg_and_date_specific_and_keeps_real_cash():
    curve = "0x00836fe54625be242bcfa286207795405ca4fd10"
    assert WINDOWS["S24"] == (date(2025, 11, 13), date(2026, 6, 25))
    for day, expected in [
        ("2026-06-25", "sky-direct-deployments"),
        ("2026-06-26", "allocation-changes"),
    ]:
        row = transfer(USDT, ALM, curve, 100 * 10**6, day)
        assert ledger([row])[0]["line_id"] == expected
        assert ledger([row])[0]["amount_usd"] == "-100"
    assert (
        ledger([transfer(USDS, ALM, curve, 100 * 10**18, "2026-06-25")])[0]["line_id"]
        == "allocation-changes"
    )


def test_verified_treasury_sale_proceeds_are_not_income_again():
    ops = "0x2e1b01adabb8d4981863394bea23a1263cbaedfc"
    cow = "0x9008d19f58aabd9ed0d60971565aa8510560ab41"
    row = transfer(USDC, cow, ops, 76629483846)
    entries = ledger(
        [row], disposals=[dict(chain="ethereum", tx_hash=row["tx_hash"], amount_usd="76629.483846")]
    )
    assert entries[0]["line_id"] == "treasury-disposals"
    assert entries[0]["amount_usd"] == "76629.483846"


def test_non_cash_compensation_missing_is_not_zero_and_is_added_back_once():
    from settle.revenue.cash_flow.config import layout

    cells = {r["id"]: metric("0") for r in layout()}
    cells["net-income"] = metric("80")
    cells["contributor-spk"] = metric("20")
    subtotals(cells)
    assert amount(cells["net-operating"]) == 100
    cells["contributor-spk"] = metric(reason="awaiting_approved_expense_sheet")
    subtotals(cells)
    assert cells["net-operating"]["status"] == "partial"
    assert amount(cells["net-operating"]) == 80
