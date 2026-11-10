"""Economic capital reclassifications for queued shares and fixed cooldown cash.

Custody exits/returns cancel their original share-flow capital at the SAME
NAV clock. Actual payments remove their actual underlying amount. Using the
change in marked pending value instead would turn queued yield into principal.
"""
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime, time
from decimal import Decimal

from ..extract import hypersync, hypersync_store, rpc
from ..extract._keccak import keccak256
from ..extract.cache import cached
from ..extract.transfer_logs import TRANSFER_TOPIC0
from .async_protocols import address_view
from .prices import par_stable_price

D = Decimal
ZERO_TOPIC = '0x' + '0' * 64


def topic(signature):
    return '0x' + keccak256(signature.encode()).hex()


def who(address):
    return '0x' + address.hex[2:].zfill(64)


def words(row, count):
    if len(row.data) != 2 + 64 * count or not row.data.startswith('0x'):
        raise ValueError('Invalid async capital event payload')
    return tuple(int(row.data[2 + n * 64:2 + (n + 1) * 64], 16) for n in range(count))


@cached(source_id='async-allocation.receipt.v1')
def receipt(chain, tx):
    result = rpc._post(rpc.rpc_url(chain), 'eth_getTransactionReceipt', [tx])
    if result is None or result['status'] != '0x1' or result['transactionHash'].lower() != tx:
        raise ValueError('Missing/failed async capital transaction receipt')
    return result


def unique_rows(rows, pin):
    unique = {}
    for row in rows:
        if not 0 < row.block_number <= pin or row.block_time <= 0 or not row.transaction_hash:
            raise ValueError('Invalid async event pin/identity')
        key = row.block_number, row.log_index
        if key in unique and unique[key] != row:
            raise ValueError('Conflicting duplicate async event')
        unique[key] = row
    return sorted(unique.values(), key=lambda r: (r.block_number, r.log_index))


def capital_events(prime, venue, period, *, opening_block, block_resolver, nav_at_block):
    """Return period corrections plus source identities; check opening/closing claims."""
    policy = venue.async_allocation
    if policy is None or policy.kind not in {'maple', 'ethena'}:
        return []
    owner = venue.holder_override or prime.alm[venue.chain]
    owner_topic, token = who(owner), venue.token.address.hex
    pin = period.pin_blocks[venue.chain]
    share_selections = [
        {'address': [token], 'topics': [[TRANSFER_TOPIC0], [owner_topic]]},
        {'address': [token], 'topics': [[TRANSFER_TOPIC0], [], [owner_topic]]},
    ]
    fetch = hypersync_store.fetch_logs
    fields = [*hypersync._DEFAULT_LOG_FIELDS, 'transaction_hash']
    shares = unique_rows(fetch(venue.chain.value, share_selections, 0, pin, log_fields=fields), pin)
    price = par_stable_price(venue.underlying)
    share_scale, asset_scale = D(10**venue.token.decimals), D(10**venue.underlying.decimals)
    created = topic('RequestCreated(uint256,address,uint256)')
    processed = topic('RequestProcessed(uint256,address,uint256,uint256)')
    manual = topic('ManualSharesIncreased(uint256,address,uint256)')
    requested = topic('RedeemRequested(address,uint256)')
    redeemed = topic('RedeemProcessed(address,uint256,uint256)')
    withdraw = topic('Withdraw(address,address,address,uint256,uint256)')
    if policy.kind == 'maple':
        escrows = {m.address.hex for m in policy.managers}
        managers = {address_view(venue.chain, m.address, 'poolManager()', pin).hex for m in policy.managers
                    if rpc.is_contract_deployed(venue.chain, m.address, pin)}
        selections = [
            {'address': sorted(escrows), 'topics': [[created, processed, manual], [], [owner_topic]]},
            {'address': sorted(managers), 'topics': [[requested, redeemed], [owner_topic]]},
            {'address': [venue.underlying.address.hex], 'topics': [[TRANSFER_TOPIC0], [who(venue.token.address)], [owner_topic]]},
        ]
    else:
        silo = address_view(venue.chain, venue.token.address, 'silo()', pin)
        escrows, managers = {silo.hex}, set()
        selections = [
            {'address': [token], 'topics': [[withdraw], [], [], [owner_topic]]},
            {'address': [venue.underlying.address.hex], 'topics': [[TRANSFER_TOPIC0], [who(silo)], [owner_topic]]},
        ]
    legacy = {topic(sig.replace('uint256,', 'uint128,', 1)): topic(sig) for sig in [
        'RequestCreated(uint256,address,uint256)', 'RequestProcessed(uint256,address,uint256,uint256)',
        'ManualSharesIncreased(uint256,address,uint256)']}
    if policy.kind == 'maple':
        selections[0]['topics'][0] += list(legacy)
    rows = unique_rows([*shares, *fetch(venue.chain.value, selections, 0, pin, log_fields=fields)], pin)
    rows = [replace(r, topic0=legacy.get(r.topic0, r.topic0)) for r in rows]
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row.block_number, row.transaction_hash)].append(row)
    pending, opening_pending = 0, 0
    owned_requests = {}
    emitted = []
    for (block, tx), logs in sorted(grouped.items()):
        async_raw = 0
        paid = 0
        identities = []
        if policy.kind == 'maple':
            creations = [r for r in logs if r.address in escrows and r.topic0 == created]
            requests = [r for r in logs if r.address in managers and r.topic0 == requested]
            if sum(words(r, 1)[0] for r in creations) != sum(words(r, 1)[0] for r in requests):
                raise ValueError('Maple request lacks its owner-specific queue creation')
            new = sum(words(r, 1)[0] for r in creations)
            for r in creations:
                key = r.address, r.topic1
                if key in owned_requests:
                    raise ValueError('Duplicate owner request ID')
                owned_requests[key] = words(r, 1)[0]
            all_processed = [r for r in logs if r.address in escrows and r.topic0 == processed]
            for r in all_processed:
                key = r.address, r.topic1
                quantity = words(r, 2)[0]
                if key not in owned_requests or owned_requests[key] < quantity:
                    raise ValueError('Processing lacks its owner-specific outstanding request')
                owned_requests[key] -= quantity
            made_manual = {(r.address, r.topic1) for r in logs if r.address in escrows and r.topic0 == manual}
            auto = [r for r in logs if r.address in escrows and r.topic0 == processed
                    and (r.address, r.topic1) not in made_manual]
            manual_payments = [r for r in logs if r.address in managers and r.topic0 == redeemed]
            paid = sum(words(r, 2)[1] for r in [*auto, *manual_payments])
            transfers = [r for r in logs if r.address == token and r.topic0 == TRANSFER_TOPIC0]
            returned = sum(words(r, 1)[0] for r in transfers if '0x' + r.topic1[-40:] in escrows
                           and r.topic2 == owner_topic)
            outgoing = sum(words(r, 1)[0] for r in transfers if r.topic1 == owner_topic
                           and '0x' + r.topic2[-40:] in (escrows | managers))
            burned = sum(words(r, 1)[0] for r in transfers if r.topic1 == owner_topic and r.topic2 == ZERO_TOPIC)
            if outgoing != new:
                raise ValueError('Maple queue request does not match its original owner share exit')
            if burned != sum(words(r, 2)[0] for r in manual_payments):
                raise ValueError('Maple manual redemption lacks its owner share burn')
            actual_cash = sum(words(r, 1)[0] for r in logs if r.address == venue.underlying.address.hex
                              and r.topic0 == TRANSFER_TOPIC0 and r.topic2 == owner_topic)
            if paid != actual_cash:
                raise ValueError('Maple processed assets differ from actual owner cash delivery')
            # A queued cancellation returns shares without a payment. Retain
            # its canonical receipt; ordinary inter-prime transfers are never
            # in the verified manager/escrow routing set.
            cancelled_return = returned - sum(words(r, 2)[0] for r in manual_payments)
            if cancelled_return < 0:
                raise ValueError('Manual redemption lacks its escrow share return')
            if cancelled_return:
                proof = receipt(venue.chain, tx)
                if int(proof['blockNumber'], 16) != block:
                    raise ValueError('Maple cancellation receipt has the wrong block')
                removed = {topic('RequestRemoved(uint256)'), topic('RequestRemoved(uint128)')}
                decreased = {topic('RequestDecreased(uint256,uint256)'), topic('RequestDecreased(uint128,uint256)')}
                cancelled = 0
                for r in proof['logs']:
                    if r['address'].lower() not in escrows or r['topics'][0] not in removed | decreased:
                        continue
                    key = r['address'].lower(), r['topics'][1]
                    # Other investors can be processed in the same transaction.
                    if key not in owned_requests or key in {(x.address, x.topic1) for x in all_processed}:
                        continue
                    quantity = owned_requests[key] if r['topics'][0] in removed else int(r['data'], 16)
                    if not 0 < quantity <= owned_requests[key]:
                        raise ValueError('Invalid owner-specific cancellation quantity')
                    owned_requests[key] -= quantity
                    cancelled += quantity
                if cancelled != cancelled_return:
                    raise ValueError('Returned shares differ from owner-specific cancellation evidence')
            pending += new - returned - sum(words(r, 2)[0] for r in auto)
            async_raw = returned - outgoing - burned
            identities = [f'{r.transaction_hash}:{r.log_index}' for r in [*creations, *auto, *manual_payments]]
        else:
            cooldowns = [r for r in logs if r.address == token and r.topic0 == withdraw
                         and '0x' + r.topic2[-40:] in escrows]
            added = sum(words(r, 2)[0] for r in cooldowns)
            burned = sum(words(r, 2)[1] for r in cooldowns)
            paid = sum(words(r, 1)[0] for r in logs if r.address == venue.underlying.address.hex
                       and r.topic0 == TRANSFER_TOPIC0 and r.topic2 == owner_topic)
            pending += added - paid
            actual_burn = sum(words(r, 1)[0] for r in logs if r.address == token
                              and r.topic0 == TRANSFER_TOPIC0 and r.topic1 == owner_topic and r.topic2 == ZERO_TOPIC)
            atomic_burn = sum(words(r, 2)[1] for r in logs if r.address == token and r.topic0 == withdraw
                              and '0x' + r.topic2[-40:] not in escrows)
            if actual_burn != burned + atomic_burn:
                raise ValueError('Ethena cooldown lacks canonical share burn')
            async_raw = -burned
            identities = [f'{r.transaction_hash}:{r.log_index}' for r in cooldowns]
        if pending < 0:
            raise ValueError('Negative owner-specific async claim: incomplete history')
        if block <= opening_block:
            opening_pending = pending
        day = datetime.fromtimestamp(logs[0].block_time, UTC).date()
        if period.start <= day <= period.end and (async_raw or paid):
            boundary = (pin if day == period.end else
                        min(block_resolver.block_at_or_before(venue.chain.value, datetime.combine(day, time.max, UTC)), pin))
            adjustment = -D(async_raw) / share_scale * nav_at_block(boundary) - D(paid) / asset_scale * price
            emitted.append(dict(block_date=day, block=block, transaction_hash=tx,
                                capital_adjustment_usd=adjustment, original_share_flow_raw=str(async_raw),
                                cash_paid_raw=str(paid), request_payment_identities=identities,
                                nav_block=boundary, nav=str(nav_at_block(boundary))))
    from .async_vaults import async_vault_exposure
    start = async_vault_exposure(prime, venue, opening_block)
    end = async_vault_exposure(prime, venue, pin)
    if policy.kind == 'maple':
        expected = start.redemption_shares * share_scale, end.redemption_shares * share_scale
    else:
        expected = start.redemption_cash / price * asset_scale, end.redemption_cash / price * asset_scale
    if expected != (D(opening_pending), D(pending)):
        raise ValueError('Async event history does not reproduce pinned owner claims')
    return emitted
