"""PSM3 event histories from HyperSync, retaining the existing valuation math.

ABI: sparkdotfi/spark-psm src/interfaces/IPSM3.sol. Deposit credits indexed
receiver (topic3); Withdraw debits indexed user (topic2), not its receiver.
"""

from typing import Any

from ...domain.primes import Chain
from ...domain.sky_tokens import PSM3_LEG_TOKENS
from ...extract import hypersync_store
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
        self._events: dict[str, tuple[list[Any], int]] = {}

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

    def _share_events(self, chain: str, pin: int) -> list[Any]:
        cached = self._events.get(chain)
        if cached is not None and cached[1] >= pin:
            return [r for r in cached[0] if r.block_number <= pin]
        rows = hypersync_store.fetch_logs(chain, [{
            "address": ["0x" + self._contracts[chain].hex()], "topics": [[DEPOSIT, WITHDRAW]],
        }], 0, pin)
        rows = sorted(rows, key=lambda r: (r.block_number, r.log_index))
        self._events[chain] = (rows, pin)
        return rows

    def _load_holder_history(self, chain: str, holder: bytes, *, pin_block: int) -> list[tuple[int, int]]:
        key = (chain, "0x" + holder.hex())
        cached = self._holder_history.get(key)
        if cached is not None and cached[1] >= pin_block:
            return cached[0]
        who, total, history = _addr_topic(holder), 0, []
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
        if cached is not None and cached[1] >= pin_block:
            return cached[0]
        total, history = 0, []
        for row in self._share_events(chain, pin_block):
            shares = uint_words(row.data, 2)[1]
            total += shares if row.topic0 == DEPOSIT else -shares
            history.append((row.block_number, total))
        self._pool_history[chain] = (history, pin_block)
        return history

    def _load_reserves_history(self, chain: str, psm3: bytes, *, pin_block: int) -> dict:
        key = (chain, "0x" + psm3.hex())
        cached = self._reserves_history.get(key)
        if cached and all(end >= pin_block for _, end in cached.values()):
            return cached
        tokens = ["0x" + t.address.value.hex() for t in PSM3_LEG_TOKENS[Chain(chain)].values()]
        who = _addr_topic(psm3)
        rows = hypersync_store.fetch_logs(chain, [
            {"address": tokens, "topics": [[TRANSFER_TOPIC0], [who]]},
            {"address": tokens, "topics": [[TRANSFER_TOPIC0], [], [who]]},
        ], 0, pin_block)
        histories: dict[str, tuple[list[tuple[int, int]], int]] = {t: ([], pin_block) for t in tokens}
        totals = dict.fromkeys(tokens, 0)
        unique = {(r.block_number, r.log_index): r for r in rows}
        for _, row in sorted(unique.items()):
            amount = uint_words(row.data, 1)[0]
            totals[row.address] += (amount if row.topic2 == who else 0) - (amount if row.topic1 == who else 0)
            histories[row.address][0].append((row.block_number, totals[row.address]))
        self._reserves_history[key] = histories
        return histories
