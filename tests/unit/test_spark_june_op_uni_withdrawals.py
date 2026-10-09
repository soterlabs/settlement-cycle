import json
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_june_op_uni_routes import JUNE_ROUTES
from settle.compute.spark_op_uni_withdrawals import ETH_ALM, link_spark_june_op_uni_withdrawals
from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_capital import AssetMovement as M
from settle.normalize.allocation_capital import CapitalBatch as B
from settle.normalize.allocation_capital import CapitalHistory

PROOF = json.loads(
    (Path(__file__).parents[1] / "fixtures/spark_june_op_uni_withdrawals.json").read_text()
)


def topic(signature):
    return "0x" + keccak256(signature.encode()).hex()


def dynamic(raw, index):
    offset = int.from_bytes(raw[index * 32 : (index + 1) * 32])
    size = int.from_bytes(raw[offset : offset + 32])
    result = raw[offset + 32 : offset + 32 + size]
    assert len(result) == size
    return result


def test_messages_cash_and_pinned_pending_states_prove_all_four_routes():
    for chain, holder, tx, block, stamp, legs in JUNE_ROUTES:
        rows = next(s["rows"] for s in PROOF["sources"] if s["chain"] == chain)
        assert all(
            r["transaction_hash"] == tx and r["block_number"] == block and r["block_time"] == stamp
            for r in rows
        )
        messages = [
            r
            for r in rows
            if r["address"] == "0x4200000000000000000000000000000000000016"
            and r["topic0"]
            == topic("MessagePassed(uint256,address,address,uint256,uint256,bytes,bytes32)")
        ]
        assert len(messages) == 2
        for row, (local, remote, amount, arrival, *_) in zip(messages, legs, strict=True):
            raw = bytes.fromhex(row["data"][2:])
            payload = dynamic(raw, 2)
            assert (
                payload[:4]
                == keccak256(b"relayMessage(uint256,address,address,uint256,uint256,bytes)")[:4]
            )
            message = dynamic(payload[4:], 5)
            assert (
                message[:4]
                == keccak256(b"finalizeBridgeERC20(address,address,address,address,uint256,bytes)")[
                    :4
                ]
            )
            args = message[4:]
            assert ["0x" + args[n * 32 : (n + 1) * 32][-20:].hex() for n in range(4)] == [
                remote,
                local,
                holder,
                ETH_ALM,
            ]
            assert int.from_bytes(args[128:160]) == amount
            encoded = b"".join(bytes.fromhex(row[k][2:]) for k in ("topic1", "topic2", "topic3"))
            encoded += (
                raw[:64]
                + (192).to_bytes(32)
                + len(payload).to_bytes(32)
                + payload
                + bytes((-len(payload)) % 32)
            )
            withdrawal = "0x" + keccak256(encoded).hex()
            assert withdrawal == "0x" + raw[96:128].hex()
            msg_hash = "0x" + keccak256(payload).hex()
            pinned = next(c for c in PROOF["closing_calls"] if c["message"] == msg_hash)
            assert pinned["pin"] == 25878704 and pinned["withdrawal_hash"] == withdrawal
            assert (
                pinned["successfulMessages(bytes32)"]
                == pinned["finalizedWithdrawals(bytes32)"]
                == int(arrival is not None)
            )
            assert any(
                r["address"] == local
                and r["topic0"] == TRANSFER_TOPIC0
                and r["topic1"].endswith(holder[2:])
                and int(r["topic2"], 16) == 0
                and int(r["data"], 16) == amount
                for r in rows
            )
            relays = [
                r
                for r in PROOF["ethereum"]
                if r["topic0"] == topic("RelayedMessage(bytes32)") and r["topic1"] == msg_hash
            ]
            assert len(relays) == int(arrival is not None)
            if arrival:
                assert relays[0]["transaction_hash"] == arrival
                assert any(
                    r["transaction_hash"] == arrival
                    and r["topic0"] == TRANSFER_TOPIC0
                    and r["address"] == remote
                    and r["topic2"].endswith(ETH_ALM[2:])
                    and int(r["data"], 16) == amount
                    for r in PROOF["ethereum"]
                )


def history(route):
    chain, _, tx, _, _, legs = route
    ids = {chain + ":" + tx, *("ethereum:" + leg[3] for leg in legs if leg[3])}
    bs = []
    for row in PROOF["batches"]:
        if row["identity"] not in ids:
            continue
        b = dict(row)
        b["day"] = date.fromisoformat(b["day"])
        b["minted"] = D(b["minted"])
        b["minted_by_ilk"] = {k: D(v) for k, v in b["minted_by_ilk"].items()}
        b["external_funding"] = ()
        b["movements"] = tuple(
            M(
                m["account"],
                D(m["value_before"]),
                D(m["change"]),
                D(m["external_income"]),
                m["preserve_basis"],
            )
            for m in b["movements"]
        )
        bs.append(B(**b))
    source = next(b for b in bs if b.chain == chain)
    funding = tuple(
        M(m.account, D(0), m.value_before, m.value_before / 2) for m in source.movements
    )
    debt = sum(m.change / 2 for m in funding)
    initial = B(
        "funding",
        source.day,
        source.timestamp - 1,
        chain,
        source.block - 1,
        funding,
        debt,
        minted_by_ilk={"SPARK": debt},
    )
    return CapitalHistory((initial, *bs), {}, {})


@pytest.mark.parametrize("route", JUNE_ROUTES)
def test_partial_withdrawals_keep_basis_and_do_not_invent_pending_cash(route):
    h = history(route)
    fixed = link_spark_june_op_uni_withdrawals(h)
    assert link_spark_june_op_uni_withdrawals(fixed) == fixed
    # Exercise the normal adapter dispatch too, not only the explicit helper.
    r = replay_history(h, date(2026, 6, 22), date(2026, 8, 31))
    assert not r.unmatched_receipts and not r.unmatched_outflows
    pending = [a for k, a in fixed.venue_accounts.items() if k.endswith("_1")]
    assert len(pending) == 1
    assert r.ledger.account(pending[0]).value == 10000
    assert abs(r.ledger.account(pending[0]).borrowed - D(5000)) < D("1e-18")
    assert not fixed.idle_accounts
    old_basis = sum(m.value_before / 2 for m in h.batches[1].movements)
    assert abs(sum(a.borrowed for a in r.ledger.accounts.values()) - old_basis) < D("1e-18")
    linked_source = next(
        b for b in fixed.batches if ":spark-june-op-uni-withdrawal:1" in b.identity
    )
    assert linked_source.movements[0].value_before > -linked_source.movements[0].change
    with pytest.raises(ValueError, match="append raw"):
        link_spark_june_op_uni_withdrawals(replace(fixed, batches=(*fixed.batches, h.batches[1])))
