"""Exact synchronous ERC-4626 capital; feed the existing venue revenue core.

Every owned mint/burn must match Deposit/Withdraw shares in its transaction,
and every event's assets must match direct holder/vault underlying transfers.
Unexplained movements and source failures stop calculation, without a synthetic
month-end correction or an empty-flow fallback.
"""

from collections import Counter, defaultdict
from datetime import UTC, datetime, time
from decimal import Decimal

import pandas as pd

from ..extract import hypersync, hypersync_store
from ..extract.transfer_logs import TRANSFER_TOPIC0
from .prices import par_stable_price
from .sources.hypersync_balances import _addr_topic
from .sources.hypersync_venue_events import DEPOSIT, WITHDRAW, uint_words


def cash_inflow_timeseries(prime, venue, period, *, opening_block, balance_at, fetch=None):
    if not venue.erc4626_cash_flows or venue.underlying is None:
        raise ValueError("Exact cash capital requires an opted-in synchronous ERC4626 vault")
    unit_price = par_stable_price(venue.underlying)
    holder = venue.holder_override or prime.alm[venue.chain]
    vault, asset = venue.token.address.hex, venue.underlying.address.hex
    who, zero = _addr_topic(holder.value), "0x" + "0" * 64
    closing_block = period.pin_blocks[venue.chain]
    selections = [
        {"address": [vault], "topics": [[DEPOSIT], [], [who]]},
        {"address": [vault], "topics": [[WITHDRAW], [], [], [who]]},
        {"address": [vault, asset], "topics": [[TRANSFER_TOPIC0], [who]]},
        {"address": [vault, asset], "topics": [[TRANSFER_TOPIC0], [], [who]]},
    ]
    rows = (fetch or hypersync_store.fetch_logs)(
        venue.chain.value,
        selections,
        opening_block + 1,
        closing_block,
        log_fields=[*hypersync._DEFAULT_LOG_FIELDS, "transaction_hash"],
    )
    unique = {}
    for row in rows:
        if not opening_block < row.block_number <= closing_block or not row.transaction_hash:
            raise ValueError("Unpinned or unidentified ERC4626 log")
        key = row.block_number, row.log_index
        if key in unique and unique[key] != row:
            raise ValueError("Conflicting ERC4626 log")
        unique[key] = row
    deposits, withdrawals, mints, burns = (Counter() for _ in range(4))
    assets_in, assets_out, cash_in, cash_out = (Counter() for _ in range(4))
    daily = defaultdict(int)
    daily_shares = defaultdict(int)
    for key in sorted(unique):
        row = unique[key]
        tx = row.transaction_hash
        day = datetime.fromtimestamp(row.block_time, UTC).date()
        if not period.start <= day <= period.end:
            raise ValueError("ERC4626 event date outside pinned period")
        if row.address == vault and row.topic0 in {DEPOSIT, WITHDRAW}:
            assets, shares = uint_words(row.data, 2)
            if row.topic0 == DEPOSIT:
                if row.topic2 != who:
                    raise ValueError("ERC4626 deposit owner differs from holder")
                deposits[tx] += shares
                assets_in[tx] += assets
                daily[day] += assets
                daily_shares[day] += shares
            else:
                if row.topic3 != who or (assets and row.topic2 != who):
                    raise ValueError("ERC4626 withdrawal owner/receiver differs from holder")
                withdrawals[tx] += shares
                assets_out[tx] += assets
                daily[day] -= assets
                daily_shares[day] -= shares
        elif row.topic0 == TRANSFER_TOPIC0 and row.address in {vault, asset}:
            amount = uint_words(row.data, 1)[0]
            if row.address == vault:
                if row.topic1 == who and row.topic2 == who:
                    continue
                if row.topic1 == zero and row.topic2 == who:
                    mints[tx] += amount
                elif row.topic1 == who and row.topic2 == zero:
                    burns[tx] += amount
                else:
                    raise ValueError(
                        "Direct ERC4626 share transfer needs explicit capital classification"
                    )
            elif row.topic1 == who and row.topic2 == _addr_topic(venue.token.address.value):
                cash_in[tx] += amount
            elif row.topic2 == who and row.topic1 == _addr_topic(venue.token.address.value):
                cash_out[tx] += amount
        else:
            raise ValueError("Unexpected ERC4626 event input")
    if deposits != mints or withdrawals != burns:
        raise ValueError("ERC4626 asset events do not explain gross share mints/burns")
    if assets_in != cash_in or assets_out != cash_out:
        raise ValueError("ERC4626 asset events do not match actual holder/vault cash transfers")
    opening, closing = [
        balance_at(venue.chain.value, venue.token.address.value, holder.value, block)
        for block in [opening_block, closing_block]
    ]
    if closing - opening != sum(mints.values()) - sum(burns.values()):
        raise ValueError("ERC4626 share events do not reconcile to pinned wallet balances")
    scale = Decimal(10**venue.underlying.decimals)
    frame = pd.DataFrame(
        [
            {
                "block_date": day,
                "daily_inflow": Decimal(amount) / scale * unit_price,
                "daily_net_shares_raw": daily_shares[day],
            }
            for day, amount in sorted(daily.items())
        ],
        columns=["block_date", "daily_inflow", "daily_net_shares_raw"],
    )
    frame["cum_inflow"] = frame["daily_inflow"].cumsum()
    return frame


def share_value_inflow_timeseries(cash_flows, venue, *, block_resolver, price_at_block):
    """Preserve the existing share/NAV exposure-average convention.

    Cash is exact capital for income. Repricing the already-verified share
    movements is solely for the existing average-value calculation; it must
    never replace the cash amount used to calculate revenue.
    """
    rows = []
    scale = Decimal(10**venue.token.decimals)
    for row in cash_flows.itertuples(index=False):
        stamp = datetime.combine(row.block_date, time.max, UTC)
        block = block_resolver.block_at_or_before(venue.chain.value, stamp)
        value = Decimal(int(row.daily_net_shares_raw)) / scale * price_at_block(block)
        rows.append({"block_date": row.block_date, "daily_inflow": value})
    frame = pd.DataFrame(rows, columns=["block_date", "daily_inflow"])
    frame["cum_inflow"] = frame["daily_inflow"].cumsum()
    return frame
