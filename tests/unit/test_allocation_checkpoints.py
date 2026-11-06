"""Crash/restart parity for stateful capital normalization."""

import json
from dataclasses import replace

import pytest

from settle.normalize import allocation_capital as source
from settle.normalize.allocation_checkpoints import Checkpoints
from tests.unit import test_allocation_capital_source as examples


@pytest.mark.parametrize(
    "example",
    [
        "test_queue_links_later_cash_to_original_principal",
        "test_async_subscription_and_partial_redemption_keep_funding_basis",
        "test_multiple_queue_redemptions_use_total_cash_and_share_fraction",
    ],
)
def test_mid_history_restart_matches_clean_history(monkeypatch, tmp_path, example):
    capture = source.fetch_capital_history
    save = Checkpoints.save
    resumed_calls = []

    def crash_after_commit(self, chain, end, batches, state):
        save(self, chain, end, batches, state)
        if end == 2:
            raise RuntimeError("simulated process termination")

    def checked(prime, pins, **kwargs):
        checkpoint_dir = tmp_path / str(len(resumed_calls))
        expected = capture(prime, pins, **kwargs)
        monkeypatch.setattr(Checkpoints, "save", crash_after_commit)
        with pytest.raises(RuntimeError, match="simulated"):
            capture(prime, pins, checkpoint_dir=checkpoint_dir, checkpoint_size=2, **kwargs)
        monkeypatch.setattr(Checkpoints, "save", save)
        actual = capture(prime, pins, checkpoint_dir=checkpoint_dir, checkpoint_size=2, **kwargs)
        assert actual == expected
        # A fully committed run reuses every normalized transaction.
        with monkeypatch.context() as m:
            m.setattr(
                source,
                "_capital_unit_price",
                lambda *a, **k: pytest.fail("repriced committed transaction"),
            )
            assert (
                capture(prime, pins, checkpoint_dir=checkpoint_dir, checkpoint_size=2, **kwargs)
                == expected
            )
        resumed_calls.append(True)
        return actual

    monkeypatch.setattr(source, "fetch_capital_history", checked)
    getattr(examples, example)(monkeypatch)
    assert resumed_calls


def test_changed_inputs_and_corrupt_chunks_fail_closed(monkeypatch, tmp_path):
    logs = [examples.draw()]
    prime = examples.setup(monkeypatch, logs)
    pins = {examples.Chain.ETHEREUM: 1}
    source.fetch_capital_history(prime, pins, checkpoint_dir=tmp_path, checkpoint_size=1)
    with pytest.raises(ValueError, match="inputs changed"):
        source.fetch_capital_history(prime, {examples.Chain.ETHEREUM: 2}, checkpoint_dir=tmp_path)
    logs[0] = replace(logs[0], data="0x" + "1" * 64)
    with pytest.raises(ValueError, match="inputs changed"):
        source.fetch_capital_history(prime, pins, checkpoint_dir=tmp_path)
    logs[0] = examples.draw()
    manifest = json.loads((tmp_path / "ethereum/manifest.json").read_text())
    chunk = tmp_path / "ethereum" / manifest["chunks"][0]["file"]
    chunk.write_bytes(chunk.read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum"):
        source.fetch_capital_history(prime, pins, checkpoint_dir=tmp_path)


def test_concurrent_writer_rejected(tmp_path):
    with Checkpoints(tmp_path, {}).locked():
        with pytest.raises(BlockingIOError), Checkpoints(tmp_path, {}).locked():
            pass


def test_uncommitted_chunk_is_ignored(monkeypatch, tmp_path):
    prime = examples.setup(monkeypatch, [examples.draw()])
    pins = {examples.Chain.ETHEREUM: 1}
    import settle.normalize.allocation_checkpoints as checkpoints

    write = checkpoints.atomic_write

    def fail_manifest(path, raw):
        if path.name == "manifest.json":
            raise OSError("disk failure")
        write(path, raw)

    monkeypatch.setattr(checkpoints, "atomic_write", fail_manifest)
    with pytest.raises(OSError, match="disk failure"):
        source.fetch_capital_history(prime, pins, checkpoint_dir=tmp_path, checkpoint_size=1)
    assert not (tmp_path / "ethereum/manifest.json").exists()
    monkeypatch.setattr(checkpoints, "atomic_write", write)
    assert source.fetch_capital_history(
        prime, pins, checkpoint_dir=tmp_path
    ) == source.fetch_capital_history(prime, pins)


def test_decimal_sticky_flags_do_not_invalidate_resume(monkeypatch, tmp_path):
    from decimal import Decimal, Inexact, localcontext

    prime = examples.setup(monkeypatch, [examples.draw()])
    pins = {examples.Chain.ETHEREUM: 1}
    with localcontext() as ctx:
        ctx.clear_flags()
        expected = source.fetch_capital_history(prime, pins, checkpoint_dir=tmp_path)
        Decimal(1) / Decimal(3)
        assert ctx.flags[Inexact]
        assert source.fetch_capital_history(prime, pins, checkpoint_dir=tmp_path) == expected
        ctx.prec += 1
        with pytest.raises(ValueError, match="inputs changed"):
            source.fetch_capital_history(prime, pins, checkpoint_dir=tmp_path)


def test_adapter_state_roundtrip_preserves_defaults_and_cross_chain_burns(monkeypatch):
    from collections import defaultdict
    from types import SimpleNamespace

    from settle.domain.primes import Chain
    from settle.domain.sky_tokens import USDS_ETHEREUM
    from settle.normalize.allocation_async_vaults import AsyncVaultCapital
    from settle.normalize.allocation_checkpoints import capture_state, decode, encode, restore_state
    from settle.normalize.allocation_nfts import NFTCapital

    prime = examples.setup(monkeypatch, [])
    nft = NFTCapital(prime, Chain.ETHEREUM)
    nft.liquidity["position"] = 42
    nft.owed["position"] = [7, 9]
    nft.custody_accounts["N1"].append("pending-collect")
    av = AsyncVaultCapital(Chain.ETHEREUM, {})
    av.assets[("share", "holder")] = USDS_ETHEREUM
    av.pending[("vault", "holder", "redeem")] = 13
    av.active_vaults[("share", "holder")] = "vault"
    av.metadata["vault"] = ("share", "holder")
    psm = SimpleNamespace(shares=80)
    units = defaultdict(int, {("token", "holder"): 100})
    queues = {("queue", "holder"): (("token", "holder"), 25)}
    managers = {("queue", "holder"): "manager"}
    custody = defaultdict(list, V1=["queue-account"])
    burns = [(Chain.ETHEREUM, examples.draw())]
    verified = {(Chain.ETHEREUM, "messenger", "nonce")}
    state = decode(
        json.loads(
            json.dumps(
                encode(
                    capture_state(units, queues, managers, psm, nft, av, custody, burns, verified)
                )
            )
        )
    )
    units.clear()
    queues.clear()
    managers.clear()
    custody.clear()
    burns.clear()
    verified.clear()
    nft.liquidity.clear()
    nft.owed.clear()
    psm.shares = 0
    restore_state(state, units, queues, managers, psm, nft, av, custody, burns, verified)
    assert units[("token", "holder")] == 100 and units["new"] == 0
    assert nft.liquidity["position"] == 42 and nft.owed["position"] == [7, 9]
    assert nft.owed["new"] == [0, 0]
    assert av.assets[("share", "holder")] == USDS_ETHEREUM
    assert av.pending[("vault", "holder", "redeem")] == 13 and av.pending["new"] == 0
    assert psm.shares == 80
    assert custody["V1"] == ["queue-account"] and custody["new"] == []
    assert burns == [(Chain.ETHEREUM, examples.draw())]
    assert verified == {(Chain.ETHEREUM, "messenger", "nonce")}
    assert queues[("queue", "holder")][1] == 25 and managers[("queue", "holder")] == "manager"


def test_restart_skips_completed_chain_and_restores_next_chain(monkeypatch, tmp_path):
    from settle.domain.primes import Chain, Token
    from settle.domain.sky_tokens import USDS_BY_CHAIN
    from settle.extract.transfer_logs import TRANSFER_TOPIC0
    from tests.unit.test_allocation_capital_source import (
        HOLDER,
        MANAGER,
        VAULT,
        Address,
        log,
        topic,
    )

    logs = [
        log(
            1, 1, VAULT, TRANSFER_TOPIC0, [topic(Address(bytes(20))), topic(HOLDER)], [100 * 10**18]
        ),
        log(2, 2, VAULT, TRANSFER_TOPIC0, [topic(HOLDER), topic(MANAGER)], [20 * 10**18]),
    ]
    prime = examples.setup(monkeypatch, logs)
    base_venue = replace(
        prime.venues[0],
        id="B1",
        chain=Chain.BASE,
        token=Token(Chain.BASE, VAULT, "share", 18),
        underlying=USDS_BY_CHAIN[Chain.BASE],
    )
    prime = replace(
        prime, alm={Chain.ETHEREUM: HOLDER, Chain.BASE: HOLDER}, venues=[*prime.venues, base_venue]
    )
    pins = {Chain.ETHEREUM: 2, Chain.BASE: 2}
    expected = source.fetch_capital_history(prime, pins)
    save = Checkpoints.save

    def crash(self, chain, end, batches, state):
        save(self, chain, end, batches, state)
        if chain == "base" and end == 1:
            raise RuntimeError("crash in second chain")

    monkeypatch.setattr(Checkpoints, "save", crash)
    with pytest.raises(RuntimeError, match="second chain"):
        source.fetch_capital_history(prime, pins, checkpoint_dir=tmp_path, checkpoint_size=1)
    monkeypatch.setattr(Checkpoints, "save", save)
    price = source._capital_unit_price
    calls = []

    def checked_price(v, block, **kw):
        calls.append((v.chain, block))
        return price(v, block, **kw)

    monkeypatch.setattr(source, "_capital_unit_price", checked_price)
    assert source.fetch_capital_history(prime, pins, checkpoint_dir=tmp_path) == expected
    assert calls == [(Chain.BASE, 2)]
