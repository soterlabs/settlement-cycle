"""Venue-specific event inputs, equivalent to the retained Dune SQL oracles.

All scans use the shared incremental log store. Missing history/transport
failures propagate: they must never become zero capital flows or zero rewards.
"""

from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd

from ...extract import hypersync, hypersync_store
from ...extract.transfer_logs import TRANSFER_TOPIC0
from .hypersync_balances import _addr_topic, _default_start_block

DEPOSIT = "0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7"
WITHDRAW = "0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc9667232c8db"
CLAIMED = "0xf7a40077ff7a04c7e61f6f26fb13774259ddf1b6bce9ecf26a8276cdd3992683"
MINT = "0x458f5fa412d0f69b08dd84872b0215675cc67bc1d5b6fd93300a1c3878b86196"


def uint_words(data: str, count: int) -> list[int]:
    raw = data.removeprefix("0x")
    if len(raw) != count * 64:
        raise ValueError(f"Expected {count} ABI words, got {len(raw)} hex digits")
    return [int(raw[i:i + 64], 16) for i in range(0, len(raw), 64)]


def window_logs(chain: str, selections: list[dict[str, Any]], start: date,
                pin: int, end: date | None = None, *, transactions: bool = False
                ) -> list[hypersync.LogRow]:
    fields = [*hypersync._DEFAULT_LOG_FIELDS, "transaction_hash"] if transactions else None
    rows = hypersync_store.fetch_logs(
        chain, selections, _default_start_block(chain, start), pin, log_fields=fields,
    )
    unique = {}
    for row in rows:
        day = datetime.fromtimestamp(row.block_time, UTC).date()
        if day < start or (end is not None and day > end) or row.block_number > pin:
            continue
        if transactions and not row.transaction_hash:
            raise ValueError("Transaction hash missing from venue event input")
        unique[(row.block_number, row.log_index)] = row
    return [unique[k] for k in sorted(unique)]


def centrifuge_flows(chain: str, vault: bytes, holder: bytes, start: date,
                     pin: int) -> pd.DataFrame:
    """Daily raw assets/shares, matching erc4626_centrifuge_flow.sql."""
    addr, who = "0x" + vault.hex(), _addr_topic(holder)
    logs = window_logs(chain, [
        {"address": [addr], "topics": [[DEPOSIT], [who]]},
        {"address": [addr], "topics": [[WITHDRAW], [], [who]]},
    ], start, pin)
    daily: dict[date, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for row in logs:
        assets, shares = uint_words(row.data, 2)
        values = daily[datetime.fromtimestamp(row.block_time, UTC).date()]
        offset = 0 if row.topic0 == DEPOSIT else 1
        values[offset] += assets
        values[offset + 2] += shares
    columns = ["block_date", "assets_in_raw", "assets_out_raw", "shares_in_raw", "shares_out_raw"]
    return pd.DataFrame([[d, *v] for d, v in sorted(daily.items())], columns=columns, dtype=object)


def merkl_raw(chain: str, distributor: bytes, atoken: bytes, holder: bytes,
              start: date, end: date, pin: int) -> int:
    """Wrapper JOIN and direct-receipt EXISTS semantics of merkl_claims SQL."""
    dist, token = "0x" + distributor.hex(), "0x" + atoken.hex()
    who, dist_topic, token_topic = _addr_topic(holder), _addr_topic(distributor), _addr_topic(atoken)
    logs = window_logs(chain, [
        {"address": [dist], "topics": [[CLAIMED], [who]]},
        {"address": [token], "topics": [[MINT], [], [who]]},
        {"address": [token], "topics": [[TRANSFER_TOPIC0], [dist_topic], [who]]},
    ], start, pin, end, transactions=True)
    claims = [r for r in logs if r.address == dist and r.topic0 == CLAIMED]
    # SQL's wrapper INNER JOIN preserves multiplicity, whereas its direct
    # EXISTS predicate counts each receipt once even with repeated markers.
    mints = Counter((r.transaction_hash, r.topic1) for r in logs
                    if r.address == token and r.topic0 == MINT)
    direct_txs = {r.transaction_hash for r in claims if r.topic2 == token_topic}
    total = sum(uint_words(r.data, 1)[0] * mints[(r.transaction_hash, r.topic2)]
                for r in claims if r.topic2 != token_topic)
    total += sum(uint_words(r.data, 1)[0] for r in logs
                 if r.address == token and r.topic0 == TRANSFER_TOPIC0
                 and r.transaction_hash in direct_txs)
    return total


def transfer_raw(chain: str, token: bytes, sender: bytes, holder: bytes,
                 start: date, end: date, pin: int) -> int:
    logs = window_logs(chain, [{"address": ["0x" + token.hex()],
                               "topics": [[TRANSFER_TOPIC0], [_addr_topic(sender)], [_addr_topic(holder)]]}],
                       start, pin, end)
    return sum(uint_words(r.data, 1)[0] for r in logs)
