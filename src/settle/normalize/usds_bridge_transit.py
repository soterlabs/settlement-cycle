"""Eligible native USDS in canonical bridges (Atlas A.2.4.2.4).

Gateway events authenticate token, sender, receiver and completed asset delivery.
Only transfers between the SAME prime's ALMs qualify. Pool withdrawals followed
by a bridge enter this same raw-USDS boundary; LP tokens/sUSDS/USDC do not.
Identical fungible payloads use an ordered multiset: matching their delivery
order cannot change aggregate pending USDS. Never pair by amount/date proximity.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd

from ..domain.primes import Chain
from ..domain.sky_tokens import USDS_BY_CHAIN
from ..extract import hypersync, hypersync_store
from ..extract._keccak import keccak256


def topic(signature):
    return '0x' + keccak256(signature.encode()).hex()


OP_SEND = topic('ERC20BridgeInitiated(address,address,address,address,uint256,bytes)')
OP_RECEIVE = topic('ERC20BridgeFinalized(address,address,address,address,uint256,bytes)')
ARB_DEPOSIT = topic('DepositInitiated(address,address,address,uint256,uint256)')
ARB_DEPOSIT_DONE = topic('DepositFinalized(address,address,address,uint256)')
ARB_WITHDRAW = topic('WithdrawalInitiated(address,address,address,uint256,uint256,uint256)')
ARB_WITHDRAW_DONE = topic('WithdrawalFinalized(address,address,address,uint256,uint256)')

# Sky's deployed canonical bridge pairs, from spark-address-registry. Both
# directions are authenticated by counterpart-only finalization in these
# contracts. Deployment/source references are documented alongside the PR.
ROUTES = {
    Chain.ARBITRUM: ('arbitrum', '0x84b9700e28b23f873b82c1beb23d86c091b6079e', '0x13f7f24ca959359a4d710d32c715d4bce273c793'),
    Chain.BASE: ('op', '0xa5874756416fa632257eea380cabd2e87ced352a', '0xee44cdb68d618d58f75d9fe0818b640bd7b8a7b7'),
    Chain.OPTIMISM: ('op', '0x3d25b7d486cae1810374d37a48bcf0963c9b8057', '0x8f41dbf6b8498561ce1d73af16cd9c0d8ee20ba6'),
    Chain.UNICHAIN: ('op', '0xdf0535a4c96c9cd8921d8fec92a7680b281681d2', '0xa13152006d0216fe4627a0d3b006087a6a55d752'),
}
EVENTS = {OP_SEND, OP_RECEIVE, ARB_DEPOSIT, ARB_DEPOSIT_DONE, ARB_WITHDRAW, ARB_WITHDRAW_DONE}


def address(word):
    if not isinstance(word, str) or len(word) != 66 or int(word[2:26], 16):
        raise ValueError('Malformed USDS bridge address')
    return '0x' + word[-40:].lower()


def decode(row, chain, remote, family):
    """Return normalized direction/payload, or None for a different token."""
    words = ['0x' + row.data[i:i + 64] for i in range(2, len(row.data), 64)]
    if any(len(w) != 66 for w in words):
        raise ValueError('Truncated USDS bridge event')
    topics = [row.topic0, row.topic1, row.topic2, row.topic3]
    eth = USDS_BY_CHAIN[Chain.ETHEREUM].address.hex
    if family == 'op':
        local = USDS_BY_CHAIN[chain].address.hex
        other = USDS_BY_CHAIN[remote].address.hex
        if address(topics[1]) != local or address(topics[2]) != other:
            return None
        if len(words) < 4 or int(words[2], 16) != 96:
            raise ValueError('Invalid USDS bridge dynamic payload')
        size = int(words[3], 16)
        extra = row.data[2 + 4 * 64:2 + 4 * 64 + size * 2]
        if len(extra) != size * 2 or len(words) != 4 + (size + 31) // 32:
            raise ValueError('Truncated USDS bridge extraData')
        return row.topic0 == OP_SEND, address(topics[3]), address(words[0]), int(words[1], 16), extra
    if row.topic0 == ARB_DEPOSIT_DONE:
        if address(topics[1]) != eth:
            return None
        if len(words) != 1:
            raise ValueError('Invalid Arbitrum deposit completion')
        return False, address(topics[2]), address(topics[3]), int(words[0], 16), ''
    if not words or address(words[0]) != eth:
        return None
    expected = 3 if row.topic0 == ARB_WITHDRAW else 2
    if len(words) != expected:
        raise ValueError('Invalid Arbitrum USDS bridge payload')
    return row.topic0 in (ARB_DEPOSIT, ARB_WITHDRAW), address(topics[1]), address(topics[2]), int(words[-1], 16), ''


def collect_transfers(prime, period, *, fetch=None):
    """Read complete gateway histories at the report's pins; retain open claims.

    Starting at deployment/genesis also covers claims sent before the report
    month/year. Missing/failed reads raise; they never imply zero transit.
    """
    fetch = fetch or hypersync_store.fetch_logs
    events = []
    seen = {}
    active_chains = prime.chains_at(period.end)
    for l2, (family, l1_gateway, l2_gateway) in ROUTES.items():
        if (Chain.ETHEREUM not in prime.alm or l2 not in prime.alm
                or l2 not in active_chains):
            continue
        for chain, remote, gateway in [(Chain.ETHEREUM, l2, l1_gateway), (l2, Chain.ETHEREUM, l2_gateway)]:
            if chain not in period.pin_blocks:
                raise ValueError(f'USDS bridge missing chain pin: {chain.value}')
            allowed = ({OP_SEND, OP_RECEIVE} if family == 'op' else
                       {ARB_DEPOSIT, ARB_WITHDRAW_DONE} if chain == Chain.ETHEREUM else
                       {ARB_WITHDRAW, ARB_DEPOSIT_DONE})
            rows = fetch(chain.value, [{'address': [gateway], 'topics': [sorted(allowed)]}],
                         0, period.pin_blocks[chain],
                         log_fields=[*hypersync._DEFAULT_LOG_FIELDS, 'transaction_hash'])
            for row in rows:
                if row.address.lower() != gateway or row.topic0 not in allowed or row.block_number > period.pin_blocks[chain]:
                    raise ValueError('USDS bridge event outside requested scope')
                value = decode(row, chain, remote, family)
                if value is None:
                    continue
                send, sender, receiver, amount, extra = value
                source, destination = (chain, remote) if send else (remote, chain)
                if sender != prime.alm[source].hex or receiver != prime.alm[destination].hex:
                    continue
                if amount <= 0 or not row.transaction_hash or row.block_time <= 0:
                    raise ValueError('Invalid USDS bridge amount/transaction identity')
                key = (l2.value, source.value, sender, receiver, amount, extra)
                identity = (chain.value, row.block_number, row.log_index)
                item = (key, send, row.block_time, row.transaction_hash)
                if identity in seen:
                    if seen[identity] != item:
                        raise ValueError('Conflicting USDS bridge event identity')
                    continue
                seen[identity] = item
                events.append((row.block_time, not send, chain.value, row.block_number, row.log_index, key, row))
    queues = defaultdict(deque)
    transfers = []
    for _, receive, _chain, _, _, key, row in sorted(events):
        if not receive:
            record = dict(source_chain=key[1], destination_chain=(Chain.ETHEREUM.value if key[1] != Chain.ETHEREUM.value else key[0]),
                          sender=key[2], recipient=key[3], amount_usds=str(Decimal(key[4]) / Decimal(10)**18),
                          sent_at=row.block_time, received_at=None,
                          source_transaction=row.transaction_hash, source_log_index=row.log_index,
                          destination_transaction=None)
            queues[key].append(record)
            transfers.append(record)
        else:
            if not queues[key]:
                raise ValueError('USDS delivery has no matching authenticated source transfer')
            record = queues[key].popleft()
            record.update(received_at=row.block_time, destination_transaction=row.transaction_hash)
    return transfers


def daily_exposure(period, transfers):
    """Closing source-chain claim on [send date, receipt date); no overlap."""
    rows = []
    for i in range(period.n_days):
        day = period.start + timedelta(days=i)
        by_chain = defaultdict(Decimal)
        for transfer in transfers:
            start = datetime.fromtimestamp(transfer['sent_at'], UTC).date()
            end = (datetime.fromtimestamp(transfer['received_at'], UTC).date()
                   if transfer['received_at'] is not None else None)
            amount = Decimal(transfer['amount_usds'])
            if (not amount.is_finite() or amount <= 0 or transfer['sent_at'] <= 0
                    or (transfer['received_at'] is not None
                        and transfer['received_at'] < transfer['sent_at'])):
                raise ValueError('Invalid USDS bridge claim')
            if start <= day and (end is None or day < end):
                by_chain[transfer['source_chain']] += amount
        rows.append({'block_date': day, 'cum_balance': sum(by_chain.values(), Decimal(0)),
                     'source_chain_claims': {key: str(value) for key, value in by_chain.items()}})
    return pd.DataFrame(rows)
