"""Dune-backed V3 inflow source.

Tokenid discovery uses RPC (point-in-time, two snapshots — fast). Event scan
across the ~200k blocks of a settlement month uses Dune ``ethereum.logs``,
since Alchemy free tier rate-limits ``eth_getLogs`` to 10 blocks per request.

The class implements ``IV3PositionSource`` so it's drop-in compatible with
the RPC variant — same Protocol shape, Dune underneath only for the range
scan.
"""

from __future__ import annotations

from ...domain.primes import Address, Chain
from ...extract import uniswap_v3 as v3
from ...extract.dune import execute_query
from ._paths import QUERIES_DIR
from .uniswap_v3 import RPCUniswapV3PositionSource


class DuneV3InflowSource(RPCUniswapV3PositionSource):
    """Inherits ``positions_in_pool`` from the RPC parent (point-in-time
    snapshot) and overrides ``liquidity_events_in_pool`` to scan
    ``ethereum.logs`` via Dune instead of paginated ``eth_getLogs``."""

    def liquidity_events_in_pool(
        self,
        chain: str,
        owner: bytes,
        pool: bytes,
        from_block: int,
        to_block: int,
    ) -> list[v3.V3LiquidityEvent]:
        chain_e = Chain(chain)
        owner_a = Address(owner)
        pool_a = Address(pool)
        nfpm = self._nfpm(chain_e)

        # 1. Discover tokenIds at both period boundaries (positions open at
        #    SoM, EoM, or both — captures positions that survived the period).
        pool_state = v3.read_pool_state(chain_e, pool_a, to_block)
        pool_target = (pool_state.token0.value, pool_state.token1.value, pool_state.fee)

        token_ids: set[int] = set()
        for boundary in (from_block, to_block):
            n = v3.nfpm_balance_of(chain_e, nfpm, owner_a, boundary)
            for i in range(n):
                tid = v3.token_of_owner_by_index(chain_e, nfpm, owner_a, i, boundary)
                pos = v3.read_position(chain_e, nfpm, tid, boundary)
                if (pos.token0.value, pos.token1.value, pos.fee) == pool_target:
                    token_ids.add(tid)

        if not token_ids:
            return []

        # 2. Build the ``IN (...)`` parameter for the SQL query — comma-
        #    separated 32-byte-padded hex tokenIds.
        padded = ", ".join(
            "0x" + format(tid, "x").rjust(64, "0") for tid in sorted(token_ids)
        )

        df = execute_query(
            QUERIES_DIR / "v3_liquidity_events.sql",
            params={
                "nfpm": nfpm.value,            # bytes → 0x-prefixed text
                "from_block": from_block,
                "token_ids_padded": padded,
            },
            pin_block=to_block,
        )
        if df.empty:
            return []

        # 3. Decode each row through the existing log decoder.
        events = [
            v3._decode_liquidity_log({
                "blockNumber": hex(int(row["block_number"])),
                "transactionHash": row["tx_hash"],
                "logIndex": hex(int(row["log_index"])),
                "topics": [row["topic0"], row["topic1"]],
                "data": row["data"],
            })
            for _, row in df.iterrows()
        ]
        events.sort(key=lambda e: (e.block_number, e.log_index))
        return events
