"""Match CCTP v1 burns and mints by authenticated message identity.

Never match bridges by amount/date proximity. Source domain + nonce + messenger
identify a transfer; the receiver body must also agree on token, owner, amount,
and recipient. In-flight funds remain a separate beneficial-custody account.
"""
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from ..domain.primes import Address
from ..extract import hypersync, hypersync_store, rpc
from ..extract._keccak import keccak256

DEPOSIT_FOR_BURN = "0x" + keccak256(
    b"DepositForBurn(uint64,address,uint256,address,bytes32,uint32,bytes32,bytes32)"
).hex()
MESSAGE_RECEIVED = "0x" + keccak256(b"MessageReceived(address,uint32,uint64,bytes32,bytes)").hex()


def _view(chain, address, signature, block):
    raw = rpc.eth_call(chain, Address.from_str(address),
                       "0x" + keccak256(signature.encode())[:4].hex(), block)
    if len(raw) != 66:
        raise ValueError(f"Invalid bridge metadata response: {signature}")
    return raw


def funded_cctp_burns(chain, rows, tracked_usdc, verified):
    """Validate each new message against an unconsumed payment to TokenMinter.

    Circle TokenMessenger transfers USDC to localMinter(), not to itself.
    https://github.com/circlefin/evm-cctp-contracts/blob/a92a2b4e7e6ef99bf0b05dca71780f5ec190e729/src/TokenMessenger.sol
    Real multi-message example: Ethereum transaction
    0x5332b54a1b545921fb625dd526cd5be2a4ead0cffd8e635f925e18d90415ff63
    funds three messages received and deposited into Base Morpho in
    0xb109977fca86a06cd38ae9df0112ca273afde6f81a917f5c04112ef46eaf5d84.
    Consume individual preceding Transfer logs so equal-sized burns cannot
    reuse the same cash leg. Replacement messages may reuse original funding.
    """
    from ..extract.transfer_logs import TRANSFER_TOPIC0

    paid = []
    result = []
    minters = {}
    for row in sorted(rows, key=lambda r: r.log_index):
        if row.topic0 == TRANSFER_TOPIC0 and len(row.data) == 66:
            paid.append(row)
        if row.topic0 != DEPOSIT_FOR_BURN:
            continue
        key = ("0x" + row.topic2[-40:], "0x" + row.topic3[-40:])
        if key not in tracked_usdc:
            continue
        words = [int(row.data[i:i + 64], 16) for i in range(2, len(row.data), 64)]
        if len(words) != 5:
            raise ValueError("Invalid CCTP burn event")
        message = (chain, row.address, row.topic1)
        if message not in verified:
            if row.address not in minters:
                minters[row.address] = _view(chain, row.address, "localMinter()", row.block_number).lower()
            payment = next((r for r in paid if r.address == key[0]
                            and r.topic1 == row.topic3 and r.topic2 == minters[row.address]
                            and int(r.data, 16) == words[0]), None)
            if payment is None:
                raise ValueError("CCTP burn lacks its TokenMinter payment")
            paid.remove(payment)
            verified.add(message)
        result.append((chain, row))
    return result


def link_cctp(prime, pins, batches, burns):
    from .allocation_capital import AssetMovement

    if not burns:
        return batches
    by_id = {b.identity: b for b in batches}
    messages = {}
    nonce_topics = set()
    for chain, row in burns:
        words = [int(row.data[i:i + 64], 16) for i in range(2, len(row.data), 64)]
        if len(words) != 5:
            raise ValueError("Invalid CCTP burn event")
        transmitter = "0x" + _view(chain, row.address, "localMessageTransmitter()", row.block_number)[-40:]
        domain = int(_view(chain, transmitter, "localDomain()", row.block_number), 16)
        nonce = int(row.topic1, 16)
        key = (domain, nonce, row.address)
        identity = f"{chain.value}:{row.transaction_hash}"
        recipient = "0x" + f"{words[1]:064x}"[-40:]
        amount = Decimal(words[0]) / Decimal(10**6)  # CCTP v1 USDC
        if key in messages:
            # A replacement message does not burn new funds. Recipient changes
            # keep the original custody account and may be received only once.
            old = messages[key]
            if old["raw_amount"] != words[0] or old["token"] != row.topic2:
                raise ValueError("CCTP replacement changes token or amount")
            old["recipient"] = recipient
            continue
        account = f"cctp:{domain}:{nonce}:{row.address}"
        messages[key] = {"account": account, "amount": amount, "raw_amount": words[0],
                         "token": row.topic2, "owner": row.topic3, "recipient": recipient,
                         "destination": words[2], "received": False, "sent_at": row.block_time}
        nonce_topics.add(row.topic1)
        batch = by_id[identity]
        by_id[identity] = replace(batch, movements=(*batch.movements,
                                                   AssetMovement(account, Decimal(0), amount)))
    for chain in prime.alm:
        received = hypersync_store.fetch_logs(chain.value, [{
            "topics": [[MESSAGE_RECEIVED], [], sorted(nonce_topics)],
        }], 0, pins[chain], log_fields=[*hypersync._DEFAULT_LOG_FIELDS, "transaction_hash"])
        seen = set()
        for row in received:
            if (row.block_number, row.log_index) in seen:
                continue
            seen.add((row.block_number, row.log_index))
            raw = bytes.fromhex(row.data[2:])
            if len(raw) < 128:
                raise ValueError("Truncated CCTP receive event")
            source_domain = int.from_bytes(raw[:32], "big")
            sender = "0x" + raw[44:64].hex()
            key = (source_domain, int(row.topic2, 16), sender)
            if key not in messages:
                continue
            m = messages[key]
            offset = int.from_bytes(raw[64:96], "big")
            length = int.from_bytes(raw[offset:offset + 32], "big")
            body = raw[offset + 32:offset + 32 + length]
            if length != 132 or len(body) != length:
                raise ValueError("Invalid CCTP v1 burn-message body")
            recipient = "0x" + body[48:68].hex()
            amount = int.from_bytes(body[68:100], "big")
            if ("0x" + body[4:36].hex() != m["token"] or
                    "0x" + body[100:132].hex() != m["owner"] or
                    recipient != m["recipient"] or amount != m["raw_amount"]):
                raise ValueError("CCTP send/receive body mismatch")
            destination = int(_view(chain, row.address, "localDomain()", row.block_number), 16)
            if destination != m["destination"] or recipient != prime.alm[chain].hex:
                continue
            if m["received"]:
                raise ValueError("Duplicate CCTP message receipt")
            identity = f"{chain.value}:{row.transaction_hash}"
            if identity not in by_id:
                raise ValueError("CCTP receipt lacks a tracked asset transaction")
            batch = by_id[identity]
            timestamp = max(batch.timestamp, m["sent_at"] + 1)
            by_id[identity] = replace(batch, timestamp=timestamp,
                day=datetime.fromtimestamp(timestamp, UTC).date(), movements=(*batch.movements,
                AssetMovement(m["account"], m["amount"], -m["amount"])))
            m["received"] = True
    return list(by_id.values())
