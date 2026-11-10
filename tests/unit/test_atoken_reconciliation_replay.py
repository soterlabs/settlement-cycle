"""State completeness guards for the expanded historical component replay."""

import json
from unittest.mock import patch

import pytest

from scripts.replay_atoken_reconciliation_2026 import RetainedStates
from settle.extract import rpc
from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0


def source(rows=()):
    token = "0x" + "11" * 20
    holder = "0x" + "22" * 20
    selector = "0x" + keccak256(b"scaledBalanceOf(address)")[:4].hex() + holder[2:].zfill(64)
    query = json.dumps(["ethereum", [{"address": [token], "topics": [[TRANSFER_TOPIC0]]}], 10, 20])
    call = json.dumps(
        ["ethereum", "eth_call", [dict(to=token, data=selector, gas="0x989680"), "0xa"]],
        sort_keys=True,
        separators=(",", ":"),
    )
    document = dict(
        venues={"spark/S1": dict(chain="ethereum", token=token, holder=holder, query=query)},
        rpc={call: "0x" + format(100, "064x")},
        queries={
            query: dict(
                from_block=10,
                to_block=20,
                archive_height=20,
                selections=[{"address": [token], "topics": [[TRANSFER_TOPIC0]]}],
                rows=list(rows),
            )
        },
    )
    return RetainedStates(document), bytes.fromhex(token[2:]), bytes.fromhex(holder[2:])


def test_constant_scaled_state_requires_verified_anchor_and_complete_no_movement(monkeypatch):
    s, token, holder = source()
    monkeypatch.setenv("SETTLE_NO_CACHE", "1")
    with (
        patch.object(rpc, "rpc_url", lambda c: "retained:" + c.value),
        patch.object(rpc, "_post", s.post),
    ):
        assert s.scaled("ethereum", token, holder, 15) == 100
        assert s.scaled_proofs[0]["reference_block"] == 10
        with pytest.raises(ValueError, match="opening anchor"):
            s.scaled("ethereum", token, holder, 5)


def test_missing_state_after_a_transfer_is_not_inferred_as_zero(monkeypatch):
    r = dict(
        address="0x" + "11" * 20,
        block_number=12,
        log_index=0,
        transaction_hash="0x" + "ab" * 32,
        topic0=TRANSFER_TOPIC0,
        topic1="0x" + "0" * 64,
        topic2="0x" + "22" * 20,
    )
    s, token, holder = source([r])
    monkeypatch.setenv("SETTLE_NO_CACHE", "1")
    with pytest.raises(ValueError, match="holder event"):
        s.scaled("ethereum", token, holder, 15)


def test_expanded_generator_does_not_publish_previous_scope_totals():
    from scripts.build_jan_sep_2026_reconciliation import calculate

    with pytest.raises(RuntimeError, match="Expanded #220 replay"):
        calculate()
