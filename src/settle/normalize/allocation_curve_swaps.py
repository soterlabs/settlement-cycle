"""Actual stablecoin swap gains are earned funding, not unknown loan receipts.

Exact pool authorized by the October 30 Grove spell (swaps only):
https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20251030/GroveEthereum_20251030.sol
TokenExchange amounts must reconcile with actual ALM/pool token transfers.
Both assets use the existing par valuation. This classifies financing provenance;
it does not change reported revenue or manufacture an income receipt from NAV.
"""
from collections import defaultdict
from decimal import Decimal as D

from ..extract._keccak import keccak256
from ..extract.aave_reconstruct import _words
from ..extract.transfer_logs import TRANSFER_TOPIC0

POOL = '0xd001ae433f254283fece51d4acce8c53263aa186'
HOLDER = '0x491edfb0b8b608044e227225c715981a30f3a44e'
COINS = (('0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', 6),
         ('0x8292bb45bf1ee4d140127049757c2e0ff06317ed', 18))
EXCHANGE = '0x' + keccak256(b'TokenExchange(address,int128,uint256,int128,uint256)').hex()


def curve_swap_income(rows):
    expected, actual, net = defaultdict(int), defaultdict(int), defaultdict(int)
    gains = defaultdict(D)
    meta = {}
    seen = {}
    for r in rows:
        key = (r.transaction_hash, r.log_index)
        if key in seen:
            if seen[key] != r:
                raise ValueError('Conflicting Curve swap event identity')
            continue
        seen[key] = r
        if r.topic0 == TRANSFER_TOPIC0 and r.address in {t for t, _ in COINS}:
            if len(r.data) != 66:
                raise ValueError('Invalid Curve swap token transfer')
            sender, recipient = '0x' + r.topic1[-40:], '0x' + r.topic2[-40:]
            amount = int(r.data, 16)
            actual[(r.transaction_hash, r.address, sender, recipient)] += amount
            if recipient == HOLDER:
                net[(r.transaction_hash, r.address)] += amount
            if sender == HOLDER:
                net[(r.transaction_hash, r.address)] -= amount
        if r.address != POOL or r.topic0 != EXCHANGE or not r.topic1.endswith(HOLDER[2:]):
            continue
        words = _words(r.data)
        if len(words) != 4 or words[0] not in (0, 1) or words[2] != 1 - words[0]:
            raise ValueError('Unsupported Curve swap layout')
        i, sold, j, bought = words
        if min(sold, bought) <= 0:
            raise ValueError('Invalid Curve swap amount')
        source, sd = COINS[i]
        target, td = COINS[j]
        expected[(r.transaction_hash, source, HOLDER, POOL)] += sold
        expected[(r.transaction_hash, target, POOL, HOLDER)] += bought
        gain = D(bought)/10**td - D(sold)/10**sd
        if gain > 0:
            key = (r.transaction_hash, target)
            gains[key] += gain
            meta[key] = (r.block_number, r.block_time)
    if any(actual[k] != amount for k, amount in expected.items()):
        raise ValueError('Curve swap event does not match actual ALM token transfers')
    decimals = dict(COINS)
    return [(tx, *meta[(tx, token)], f'ethereum:{HOLDER}:{token}',
             D(net[(tx, token)]) / 10**decimals[token], gain)
            for (tx, token), gain in gains.items()]
