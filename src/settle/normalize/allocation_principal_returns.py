"""Apply configured day-net principal exceptions to capital-history receipts.

These exceptions only remove the income classification. They do not invent
borrowed principal: replay still needs the original custody/funding link.
"""
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from ..domain.pricing import PricingCategory
from ..extract.transfer_logs import TRANSFER_TOPIC0
from .sources.hypersync_balances import _addr_topic


def principal_return_logs(prime, chain, mapping, logs):
    overrides = prime.principal_return_overrides.get(chain, {})
    external = set(prime.external_alm_sources.get(chain, []))
    if not overrides or chain not in prime.alm:
        return {}
    holder = prime.alm[chain]
    who = _addr_topic(holder.value)
    sources = {_addr_topic(a.value): entries for a, entries in overrides.items()
               if a in external}
    totals = defaultdict(Decimal)
    receipts = defaultdict(dict)
    seen = set()
    for row in logs:
        identity = (row.block_number, row.log_index)
        if identity in seen:
            continue
        seen.add(identity)
        venue = mapping.get((row.address, holder.hex))
        if (venue is None or venue.pricing_category != PricingCategory.PAR_STABLE
                or row.topic0 != TRANSFER_TOPIC0 or len(row.data) != 66):
            continue
        incoming = row.topic2 == who and row.topic1 in sources
        outgoing = row.topic1 == who and row.topic2 in sources
        if not incoming and not outgoing:
            continue
        sender = row.topic1 if incoming else row.topic2
        day = datetime.fromtimestamp(row.block_time, UTC).date()
        key = (sender, row.address, venue.token.symbol, day)
        amount = Decimal(int(row.data, 16)) / Decimal(10**venue.token.decimals)
        totals[key] += amount if incoming else -amount
        if incoming:
            receipts[key][identity] = int(row.data, 16)
    matched = {}
    for (sender, token, symbol, day), amount in totals.items():
        entries = [entry for entry in sources[sender]
                   if entry.date == day and (not entry.token or entry.token == symbol)
                   and abs(amount - entry.amount) <= 1]
        if amount > 0 and entries:
            if len(entries) != 1:
                raise ValueError('Ambiguous principal-return exceptions')
            entry = entries[0]
            transfers = receipts[(sender, token, symbol, day)]
            total = sum(transfers.values())
            # Newer settlement configs split the September Anchorage receipt
            # into principal and interest. Preserve the earned component;
            # whole-return exceptions continue to remove all income labels.
            capital = getattr(entry, 'capital_amount', None)
            revenue = max(Decimal(0), amount - capital) if capital is not None else Decimal(0)
            scale = 10**mapping[(token, holder.hex)].token.decimals
            remaining = revenue * scale
            for i, (identity, raw) in enumerate(transfers.items()):
                part = remaining if i == len(transfers) - 1 else revenue * scale * raw / total
                matched[identity] = part
                remaining -= part
    return matched
