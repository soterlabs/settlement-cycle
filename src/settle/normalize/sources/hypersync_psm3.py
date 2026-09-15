"""PSM3 event histories from HyperSync, retaining the existing valuation math.

ABI: sparkdotfi/spark-psm src/interfaces/IPSM3.sol. Deposit credits indexed
receiver (topic3); Withdraw debits indexed user (topic2), not its receiver.
"""

from datetime import UTC, datetime, time, timedelta
from typing import Any

from ...domain.primes import Address, Chain
from ...domain.sky_tokens import PSM3_LEG_TOKENS
from ...extract import hypersync, hypersync_store, rpc
from ...extract._keccak import keccak256
from ...extract.transfer_logs import TRANSFER_TOPIC0
from .dune_psm3 import DunePsm3Source
from .hypersync_balances import _addr_topic
from .hypersync_venue_events import uint_words

DEPOSIT = "0x" + keccak256(b"Deposit(address,address,address,uint256,uint256)").hex()
WITHDRAW = "0x" + keccak256(b"Withdraw(address,address,address,uint256,uint256)").hex()


class HyperSyncPsm3Source(DunePsm3Source):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._contracts: dict[str, bytes] = {}
        self._events: dict[str, tuple[list[Any], int, int]] = {}

    def _register(self, chain: str, psm3: bytes) -> None:
        if chain in self._contracts and self._contracts[chain] != psm3:
            raise ValueError("A PSM3 source instance cannot mix contracts on one chain")
        self._contracts[chain] = psm3

    def preload(self, chain: str, holder: bytes, *, pin_block: int,
                psm3: bytes | None = None) -> None:
        if psm3 is None:
            raise ValueError("HyperSync PSM3 preload requires the configured contract")
        self._register(chain, psm3)
        super().preload(chain, holder, pin_block=pin_block, psm3=psm3)

    def shares_of(self, chain: str, psm3: bytes, holder: bytes, block: int) -> int:
        self._register(chain, psm3)
        return super().shares_of(chain, psm3, holder, block)

    def convert_to_asset_value(self, chain: str, psm3: bytes, num_shares: int, block: int) -> int:
        self._register(chain, psm3)
        return super().convert_to_asset_value(chain, psm3, num_shares, block)

    def pool_reserve_at(self, chain: str, token: bytes, psm3: bytes, block: int,
                        *, decimals: int) -> int | None:
        entry = self._reserves_history.get((chain, "0x" + psm3.hex()), {}).get("0x" + token.hex())
        if entry is None or not entry[0] or not entry[0][0][0] <= block <= entry[1]:
            # The parent assumes a lifetime history beginning at zero. Our
            # month-opening seed cannot answer dates outside its coverage.
            return None
        return super().pool_reserve_at(chain, token, psm3, block, decimals=decimals)

    def _share_events(self, chain: str, pin: int) -> list[Any]:
        opening = self._opening_block(chain, pin)
        cached = self._events.get(chain)
        if cached is not None and cached[1] == opening and cached[2] >= pin:
            return [r for r in cached[0] if r.block_number <= pin]
        rows = hypersync_store.fetch_logs(chain, [{
            "address": ["0x" + self._contracts[chain].hex()], "topics": [[DEPOSIT, WITHDRAW]],
        }], opening + 1, pin)
        rows = sorted(rows, key=lambda r: (r.block_number, r.log_index))
        self._events[chain] = (rows, opening, pin)
        return rows

    def _opening_block(self, chain: str, pin: int) -> int:
        day = datetime.fromtimestamp(hypersync.block_timestamp(chain, pin), UTC).date()
        previous = day.replace(day=1) - timedelta(days=1)
        return hypersync.find_block_at_or_before(
            chain, int(datetime.combine(previous, time(23, 59, 59), UTC).timestamp()),
        )

    def _load_holder_history(self, chain: str, holder: bytes, *, pin_block: int) -> list[tuple[int, int]]:
        key = (chain, "0x" + holder.hex())
        cached = self._holder_history.get(key)
        if cached is not None and cached[0] and cached[0][0][0] <= pin_block <= cached[1]:
            return cached[0]
        opening = self._opening_block(chain, pin_block)
        total = rpc.psm3_shares(Chain(chain), Address(self._contracts[chain]), Address(holder), opening)
        who, history = _addr_topic(holder), [(opening, total)]
        for row in self._share_events(chain, pin_block):
            if (row.topic3 if row.topic0 == DEPOSIT else row.topic2) != who:
                continue
            shares = uint_words(row.data, 2)[1]
            total += shares if row.topic0 == DEPOSIT else -shares
            history.append((row.block_number, total))
        self._holder_history[key] = (history, pin_block)
        return history

    def _load_pool_history(self, chain: str, *, pin_block: int) -> list[tuple[int, int]]:
        cached = self._pool_history.get(chain)
        if cached is not None and cached[0] and cached[0][0][0] <= pin_block <= cached[1]:
            return cached[0]
        opening = self._opening_block(chain, pin_block)
        contract = Address(self._contracts[chain])
        raw = rpc.eth_call(Chain(chain), contract,
                           "0x" + keccak256(b"totalShares()")[:4].hex(), opening)
        # A deployment-month history starts at zero. Only accept empty ABI
        # data when the contract did not exist; a deployed contract returning
        # no data must not silently reset the pool's opening share supply.
        if raw == "0x" and rpc.is_contract_deployed(Chain(chain), contract, opening):
            raise rpc.RPCError("PSM3 totalShares returned empty data for a deployed contract")
        total = rpc._decode_uint(raw)
        history = [(opening, total)]
        for row in self._share_events(chain, pin_block):
            shares = uint_words(row.data, 2)[1]
            total += shares if row.topic0 == DEPOSIT else -shares
            history.append((row.block_number, total))
        self._pool_history[chain] = (history, pin_block)
        return history

    def _load_reserves_history(self, chain: str, psm3: bytes, *, pin_block: int) -> dict:
        key = (chain, "0x" + psm3.hex())
        cached = self._reserves_history.get(key)
        if cached and all(history and history[0][0] <= pin_block <= end
                          for history, end in cached.values()):
            return cached
        # Pool reserves have millions of arbitrage transfers. Start from an
        # immutable month-opening RPC balance, then extend only this month's
        # logs. Three cached eth_calls avoid materializing the entire lifetime
        # into memory; the event store still reuses covered ranges daily.
        opening = self._opening_block(chain, pin_block)
        tokens = ["0x" + t.address.value.hex() for t in PSM3_LEG_TOKENS[Chain(chain)].values()]
        who = _addr_topic(psm3)
        rows = hypersync_store.fetch_logs(chain, [
            {"address": tokens, "topics": [[TRANSFER_TOPIC0], [who]]},
            {"address": tokens, "topics": [[TRANSFER_TOPIC0], [], [who]]},
        ], opening + 1, pin_block)
        totals = {t: rpc.balance_of(Chain(chain), Address.from_str(t), Address(psm3), opening) for t in tokens}
        histories: dict[str, tuple[list[tuple[int, int]], int]] = {
            t: ([(opening, totals[t])], pin_block) for t in tokens
        }
        unique = {(r.block_number, r.log_index): r for r in rows}
        for _, row in sorted(unique.items()):
            amount = uint_words(row.data, 1)[0]
            totals[row.address] += (amount if row.topic2 == who else 0) - (amount if row.topic1 == who else 0)
            histories[row.address][0].append((row.block_number, totals[row.address]))
        self._reserves_history[key] = histories
        return histories
