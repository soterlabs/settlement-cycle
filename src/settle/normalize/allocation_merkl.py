"""Recognize Merkl wrapper rewards in capital movements using receipt events.

The existing merkl_claims_ethereum.sql revenue policy joins Claimed.token to
Mint.caller for the same user/aToken. The Mint is usually interest accrual;
the actual new position arrives in BalanceTransfer from the wrapper's custody
account. That sender is not the distributor. Authenticate the wrapper link
and reconcile all received nominal units to the claim before marking its
scaled receipts as earned funding. Direct aToken rewards retain their existing
sender-based classification and cannot enter this wrapper branch.
"""
from collections import defaultdict

from ..extract import aave_reconstruct as aave

CLAIMED = '0xf7a40077ff7a04c7e61f6f26fb13774259ddf1b6bce9ecf26a8276cdd3992683'


def wrapper_gift_transfers(rows, distributor_topics):
    budgets = defaultdict(int)
    for claim in rows:
        if (claim.topic0 != CLAIMED
                or '0x' + claim.address[2:].rjust(64, '0') not in distributor_topics):
            continue
        if len(claim.data) != 66:
            raise ValueError('Invalid Merkl claim amount')
        tokens = {m.address for m in rows if m.transaction_hash == claim.transaction_hash
                  and m.topic0 == aave.MINT_T0 and m.topic1 == claim.topic2
                  and m.topic2 == claim.topic1 and m.address != '0x' + claim.topic2[-40:]}
        if len(tokens) > 1:
            raise ValueError('Ambiguous Merkl wrapper aToken')
        for token in tokens:
            budgets[(claim.transaction_hash, token, claim.topic1)] += int(claim.data, 16)
    gifts = set()
    for (tx, token, recipient), budget in budgets.items():
        received = [r for r in rows if r.transaction_hash == tx and r.address == token
                    and r.topic0 == aave.BT_T0 and r.topic2 == recipient]
        if not received:
            continue  # No aToken receipt; a claim marker alone is not income.
        words = [aave._words(r.data) for r in received]
        if any(len(w) != 2 or w[1] <= 0 for w in words):
            raise ValueError('Invalid Merkl wrapper BalanceTransfer')
        nominal = sum(aave.ray_mul(w[0], w[1]) for w in words)
        # Nominal reconstruction can round at most one raw unit per receipt.
        # Never classify an unrelated larger transfer based on a claim marker.
        if abs(nominal - budget) > len(received):
            raise ValueError('Merkl wrapper receipts do not reconcile to claimed rewards')
        gifts.update((r.transaction_hash, r.log_index) for r in received)
    return gifts
