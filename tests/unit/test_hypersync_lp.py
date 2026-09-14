from settle.domain.primes import Address, Chain
from settle.extract.hypersync import LogRow
from settle.normalize.sources import hypersync_lp as mod


def words(*values):
    return "0x" + "".join(format(v % (1 << 256), "064x") for v in values)


def test_v3_signed_amounts_order_and_configured_manager(monkeypatch):
    manager, owner, pool = (bytes([i]) * 20 for i in [1, 2, 3])
    source = mod.HyperSyncV3PositionSource(nfpm_per_chain={Chain.ETHEREUM: Address(manager)})
    calls = []
    def discover(chain, nfpm, holder, address, blocks):
        assert (nfpm.value, holder.value, address.value, blocks) == (manager, owner, pool, (10, 20))
        return {7}
    monkeypatch.setattr(mod.v3, "discover_pool_token_ids", discover)
    def fetch(*args, **kwargs):
        calls.append((args, kwargs))
        return [LogRow(20, 2, 100, "0x" + manager.hex(), mod.v3.TOPIC_DECREASE_LIQUIDITY,
                       words(7), None, None, words(10, 30, 40), "0xabc"),
                LogRow(11, 1, 90, "0x" + manager.hex(), mod.v3.TOPIC_INCREASE_LIQUIDITY,
                       words(7), None, None, words(20, 50, 60), "0xdef")]
    monkeypatch.setattr(mod.hypersync_store, "fetch_logs", fetch)
    events = source.liquidity_events_in_pool("ethereum", owner, pool, 10, 20)
    assert [(e.block_number, e.amount0, e.amount1) for e in events] == [(11, 50, 60), (20, -30, -40)]
    assert calls[0][0][2:] == (11, 20)
    assert calls[0][0][1][0]["address"] == ["0x" + manager.hex()]
    assert calls[0][0][1][0]["topics"][1] == [words(7)]
    assert "transaction_hash" in calls[0][1]["log_fields"]


def test_v4_filters_position_manager_and_excludes_zero_delta(monkeypatch):
    manager, pool_manager = Address(bytes([1]) * 20), Address(bytes([2]) * 20)
    pool = bytes([3]) * 32
    source = mod.HyperSyncV4PositionSource(position_manager_per_chain={Chain.ETHEREUM: manager})
    calls = []
    def fetch(*args, **kwargs):
        calls.append(args)
        return [LogRow(12, i, 100, "0x" + pool_manager.value.hex(), mod.v4.TOPIC_MODIFY_LIQUIDITY,
                       "0x" + pool.hex(), mod._addr_topic(manager.value), None,
                       words(-100, 100, delta, 7), "0xabc") for i, delta in enumerate([0, -50, 100])]
    monkeypatch.setattr(mod.hypersync_store, "fetch_logs", fetch)
    events = source._modify_liquidity_events(Chain.ETHEREUM, pool_manager, pool, 10, 20)
    assert [(e.tick_lower, e.tick_upper, e.liquidity_delta, e.token_id) for e in events] == [(-100, 100, -50, 7), (-100, 100, 100, 7)]
    assert calls[0][2:] == (11, 20)
    assert calls[0][1][0]["topics"][2] == [mod._addr_topic(manager.value)]


def test_non_ethereum_dune_oracle_is_chain_scoped(monkeypatch):
    import pandas as pd

    from settle.normalize.sources import dune_v3_inflow as oracle
    manager = Address(bytes([1]) * 20)
    source = oracle.DuneV3InflowSource(nfpm_per_chain={Chain.MONAD: manager})
    monkeypatch.setattr(oracle.v3, "discover_pool_token_ids", lambda *a: {7})
    calls = []
    def execute(path, params, pin_block):
        calls.append((path, params, pin_block))
        return pd.DataFrame()
    monkeypatch.setattr(oracle, "execute_query", execute)
    assert source.liquidity_events_in_pool("monad", bytes([2]) * 20, bytes([3]) * 20, 10, 20) == []
    path, params, pin = calls[0]
    assert path.name == "v3_liquidity_events_multichain.sql"
    assert "FROM evms.logs" in path.read_text()
    assert "blockchain = '{{chain}}'" in path.read_text()
    assert params["chain"] == "monad"
    assert params["nfpm"] == manager.value
    assert pin == 20
