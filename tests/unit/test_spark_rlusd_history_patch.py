"""Receipt correction must preserve funding and never capitalize treasury gifts."""

import sys
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

sys.path.insert(0, str(Path("scripts").resolve()))
from patch_spark_rlusd_history import (
    ALM,
    BT_T0,
    DEPOSIT,
    MINT_T0,
    SENTORA,
    SPRLUSD,
    TRANSFER,
    TREASURY,
    ZERO,
    attach,
    receipt_movements,
)

from settle.normalize.allocation_capital import CapitalBatch, CapitalHistory


def row(token, topic, sender, receiver, values, n, tx="0x01"):
    return dict(
        address=token,
        topic0=topic,
        topic1="0x" + sender[2:].zfill(64),
        topic2="0x" + receiver[2:].zfill(64),
        topic3=None,
        data="0x" + "".join(f"{v:064x}" for v in values),
        block_number=n,
        log_index=n,
        transaction_hash=tx,
        block_time=1788307200 + n,
    )


def test_sentora_deposit_and_duplicate_attachment_rejected():
    logs = [
        row(SENTORA, TRANSFER, ZERO, ALM, [100 * 10**18], 1),
        row(SENTORA, DEPOSIT, ALM, ALM, [101 * 10**18, 100 * 10**18], 1),
    ]
    logs[1]["log_index"] = 2
    patches, balances = receipt_movements(logs)
    assert balances[SENTORA] == 100 * 10**18
    movement = patches["ethereum:0x01"][1][0]
    assert movement.change == 101 and movement.external_income == 0
    batch = CapitalBatch("ethereum:0x01", date(2026, 9, 2), 1788307201, "ethereum", 1, ())
    corrected = attach(CapitalHistory((batch,), {}, {}), patches)
    with pytest.raises(ValueError, match="already present"):
        attach(corrected, patches)
    with pytest.raises(ValueError, match="Cash-bearing"):
        attach(CapitalHistory((), {}, {}), patches)


def test_mint_on_withdraw_and_treasury_gift():
    ray = 10**27
    logs = [
        row(SPRLUSD, MINT_T0, ALM, ALM, [100 * 10**18, 0, ray], 1),
        row(SPRLUSD, MINT_T0, ALM, ALM, [2 * 10**18, 3 * 10**18, ray], 2, "0x02"),
        row(SPRLUSD, BT_T0, TREASURY, ALM, [4 * 10**18, ray], 3, "0x03"),
    ]
    patches, balances = receipt_movements(logs)
    assert balances[SPRLUSD] == 103 * 10**18
    assert patches["ethereum:0x02"][1][0].change == D(-1)
    gift = patches["ethereum:0x03"][1][0]
    assert gift.change == gift.external_income == 4
    assert (
        len(attach(CapitalHistory((), {}, {}), {"ethereum:0x03": patches["ethereum:0x03"]}).batches)
        == 1
    )


def test_unknown_gift_and_conflicting_logs_fail_closed():
    r = row(SPRLUSD, BT_T0, ZERO, ALM, [1, 10**27], 1)
    with pytest.raises(ValueError, match="Unreviewed"):
        receipt_movements([r])
    with pytest.raises(ValueError, match="Conflicting"):
        receipt_movements([r, {**r, "data": "0x00"}])
