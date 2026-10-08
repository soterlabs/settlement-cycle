"""Track Grove's internal Basin shares at the PAU's actual cash boundaries.

Basin shares are not ERC20 tokens. An escrow balance is pool custody, not the
PAU's position: withdrawals can use USDC while its USDS stays in the pocket.
Use Deposit/Withdraw asset amounts and internal shares to move borrowed basis.
No pool transfers, other LP shares or earned NAV become new borrowed capital.

Contract source (deposit/withdraw and internal shares):
https://github.com/grove-labs/grove-basin/blob/633ea35ec48d70c4b37d56973bb7be96866c634e/src/GroveBasin.sol
Onboarding spell:
https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20260702/GroveEthereum_20260702.sol
"""
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal as D

from ..extract._keccak import keccak256
from ..extract.aave_reconstruct import _words

HOLDER = '0x0dcd9298e163dfd3c0b5b00f0d9093c36e40a153'
ESCROW = '0x2cd296095788a2741e72056d66b3ae1faee23ea2'
BASINS = {'0xf08943f817e1f902debc884c7b19ea5764594ac9': 'E_JTRSY_BASIN',
          '0xcba428fb052b365557daf52b744dfff20d5fbedd': 'E_BUIDL_BASIN'}
DECIMALS = {'0xdc035d45d973e3ec169d2276ddab16f1e407384f': 18,
            '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48': 6}
DEPOSIT = '0x' + keccak256(b'Deposit(address,address,address,uint256,uint256)').hex()
WITHDRAW = '0x' + keccak256(b'Withdraw(address,address,address,uint256,uint256)').hex()
FEES = '0x' + keccak256(b'FeeSharesAccrued(address,uint256)').hex()
MARKER = ':basin-shares'


def basin_account(basin):
    return f'basin:ethereum:{basin}:{HOLDER}'


def basin_events(rows):
    """Parse only the configured PAU's own funded share operations."""
    events = []
    ht = '0x' + HOLDER[2:].rjust(64, '0')
    for r in rows:
        if r.address not in BASINS:
            continue
        if r.topic0 == FEES and r.topic1 == ht:
            raise ValueError('PAU Basin fee shares require earned-share valuation')
        if r.topic0 not in (DEPOSIT, WITHDRAW) or ht not in (r.topic2, r.topic3):
            continue
        if r.topic2 != ht or r.topic3 != ht:
            raise ValueError('PAU Basin third-party deposit/withdrawal requires explicit attribution')
        asset = '0x' + r.topic1[-40:]
        words = _words(r.data)
        if asset not in DECIMALS or len(words) != 2 or min(words) <= 0:
            raise ValueError('Unsupported PAU Basin capital event')
        events.append((r.transaction_hash, r.block_number, r.block_time, r.log_index,
                       r.address, r.topic0 == DEPOSIT, asset, *words))
    return events


def link_basin_shares(history, events):
    from .allocation_capital import AssetMovement

    # This adapter never changes an unrelated prime or infers a PAU from debt.
    if not any(f'ethereum:{HOLDER}:' in a for a in history.venue_accounts.values()):
        return history
    index = {b.identity: b for b in history.batches}
    if len(index) != len(history.batches):
        raise ValueError('Duplicate Basin capital transaction')
    grouped = defaultdict(list)
    for e in sorted(events, key=lambda e: (e[1], e[3])):
        grouped['ethereum:' + e[0]].append(e)
    # A second application must leave already transformed batches untouched.
    if grouped and all(k + MARKER in index or k not in index for k in grouped):
        return history
    shares = defaultdict(int)
    replacements = {}
    venues = dict(history.venue_accounts)
    analytics = set(history.analytics_only_venues)
    covered = dict(history.covered_by_boundary)
    for identity, es in grouped.items():
        b = index.get(identity)
        if b is None:
            if identity + MARKER in index:
                raise ValueError('Partially transformed Basin history')
            continue  # A pinned history can end before later operations.
        additions = []
        for _, block, timestamp, _, basin, deposit, asset, assets, units in es:
            if b.chain != 'ethereum' or b.block != block or b.timestamp != timestamp:
                raise ValueError('Basin event does not match capital transaction')
            if asset not in DECIMALS or min(assets, units) <= 0:
                raise ValueError('Unsupported Basin asset or share amount')
            if not deposit and units > shares[basin]:
                raise ValueError('Basin withdrawal exceeds observed PAU shares')
            value = D(assets) / 10**DECIMALS[asset]
            before = D(shares[basin]) * value / D(units)
            # Existing basis follows the redeemed fraction; any excess cash is
            # earned value. On deposits the full amount paid remains principal.
            additions.append(AssetMovement(basin_account(basin), before,
                                           value if deposit else -value))
            shares[basin] += units if deposit else -units
            vid = BASINS[basin]
            previous = venues.get(vid)
            expected_escrow = f'ethereum:{ESCROW}:0xdc035d45d973e3ec169d2276ddab16f1e407384f'
            if previous not in (None, basin_account(basin)):
                raise ValueError('Basin allocation ID already owns another account')
            if vid == 'E_JTRSY_BASIN' and 'E41' in venues:
                if venues['E41'] != expected_escrow:
                    raise ValueError('E41 does not identify the reviewed Basin escrow')
                del venues['E41']
                covered['E41'] = vid
            venues[vid] = basin_account(basin)
            analytics.add(vid)  # No NAV/APY claim from cash event marks alone.
        replacements[identity] = replace(b, identity=identity + MARKER,
                                         movements=(*b.movements, *additions))
    if not replacements:
        return history
    # E41's old escrow observation is replaced, not added to LP ownership.
    # The pool can service another LP, rebalance or hold earned interest there.
    pool_holders = {ESCROW, *BASINS}
    batches = []
    for b in history.batches:
        b = replacements.get(b.identity, b)
        movements = tuple(m for m in b.movements if not any(
            m.account.startswith(f'ethereum:{h}:') for h in pool_holders))
        batches.append(replace(b, movements=movements))
    return replace(history, batches=tuple(batches), venue_accounts=venues,
                   analytics_only_venues=tuple(sorted(analytics)), covered_by_boundary=covered)
