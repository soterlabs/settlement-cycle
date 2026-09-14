"""HyperSync log scans with the existing RPC LP valuation/decoding logic."""

from ...domain.primes import Address, Chain
from ...extract import hypersync, hypersync_store
from ...extract import uniswap_v3 as v3
from ...extract import uniswap_v4 as v4
from .hypersync_balances import _addr_topic
from .uniswap_v3 import RPCUniswapV3PositionSource
from .uniswap_v4 import RPCUniswapV4PositionSource


class HyperSyncV3PositionSource(RPCUniswapV3PositionSource):
    def liquidity_events_in_pool(self, chain: str, owner: bytes, pool: bytes,
                                 from_block: int, to_block: int) -> list[v3.V3LiquidityEvent]:
        chain_e = Chain(chain)
        nfpm = self._nfpm(chain_e)
        ids = v3.discover_pool_token_ids(chain_e, nfpm, Address(owner), Address(pool),
                                        (from_block, to_block))
        if not ids:
            return []
        logs = hypersync_store.fetch_logs(chain, [{
            "address": ["0x" + nfpm.value.hex()],
            "topics": [[v3.TOPIC_INCREASE_LIQUIDITY, v3.TOPIC_DECREASE_LIQUIDITY],
                       ["0x" + format(tid, "064x") for tid in sorted(ids)]],
        }], from_block + 1, to_block,
            log_fields=[*hypersync._DEFAULT_LOG_FIELDS, "transaction_hash"])
        return [v3._decode_liquidity_log({
            "blockNumber": hex(r.block_number), "transactionHash": r.transaction_hash,
            "logIndex": hex(r.log_index), "topics": [r.topic0, r.topic1], "data": r.data,
        }) for r in sorted(logs, key=lambda r: (r.block_number, r.log_index))]


class HyperSyncV4PositionSource(RPCUniswapV4PositionSource):
    def _modify_liquidity_events(self, chain_e: Chain, pool_manager: Address,
                                 pool_id: bytes, from_block: int,
                                 to_block: int) -> list[v4.V4LiquidityEvent]:
        logs = hypersync_store.fetch_logs(chain_e.value, [{
            "address": ["0x" + pool_manager.value.hex()],
            "topics": [[v4.TOPIC_MODIFY_LIQUIDITY], ["0x" + pool_id.hex()],
                       [_addr_topic(self._position_manager(chain_e).value)]],
        }], from_block + 1, to_block,
            log_fields=[*hypersync._DEFAULT_LOG_FIELDS, "transaction_hash"])
        events = []
        for r in logs:
            event = v4.decode_modify_liquidity_log({
                "blockNumber": r.block_number, "transactionHash": r.transaction_hash,
                "logIndex": r.log_index, "data": r.data,
            })
            if event is not None:
                events.append(event)
        return sorted(events, key=lambda e: (e.block_number, e.log_index))
