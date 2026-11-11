"""Independent archive-state capture for native-USDS wallet deductions.

No event-derived balance reconstruction and no fallback/carry-forward/zero on
failure. This is a raw-evidence producer, not a passing whole-portfolio review.
"""
import re
from datetime import UTC, date, datetime, time

from settle.controls.assets import AssetControlError, require
from settle.controls.boundaries import block_hash, rpc_quantity
from settle.domain.sky_tokens import USDS_BY_CHAIN
from settle.extract import rpc
from settle.normalize.usds_bridge_transit import usds_amount
from settle.revenue.balance_sheet_sources import CHAIN_IDS


def check_boundary(day, block, headers, chain, chain_id, head):
    boundary = int(datetime.combine(datetime.fromisoformat(day).date(), time.max, UTC).timestamp())
    require(all(type(n) is int for n in (chain_id, head, block))
            and chain_id == CHAIN_IDS[chain] and head >= block + 65, 'Wrong chain or unfinalized wallet evidence')
    require(len(headers) == 2 and all(headers), f'Missing block headers: {day}/{chain}')
    require(rpc_quantity(headers[0]['number']) == block
            and rpc_quantity(headers[1]['number']) == block + 1
            and block_hash(headers[1]['parentHash']) == block_hash(headers[0]['hash'])
            and block_hash(headers[1]['hash'])
            and rpc_quantity(headers[0]['timestamp']) <= boundary < rpc_quantity(headers[1]['timestamp']),
            f'Block is not the UTC daily close: {day}/{chain}')


def capture_wallets(prime, daily_pins, *, call=None):
    # Direct provider calls deliberately avoid the normal event/cache balance
    # path: this control must be independent of the financial input it checks.
    if call is None:
        def call(chain, method, params):
            return rpc._post(rpc.rpc_url(chain), method, params)
    require(bool(daily_pins), 'Wallet capture requires explicit daily pins')
    last_day = max(date.fromisoformat(day) for day in daily_pins)
    rows = []
    identities = {}
    for day, pins in daily_pins.items():
        for chain, holder in prime.alm.items():
            if prime.alm_start_dates.get(chain, date.min) > last_day:
                continue
            if chain not in USDS_BY_CHAIN:
                continue  # No native-USDS exemption on this chain, not a portfolio exclusion.
            require(chain.value in pins, f'Missing wallet control pin: {day}/{chain.value}')
            block = pins[chain.value]
            require(type(block) is int and block >= 0, 'Invalid wallet control block')
            if block == 0:
                # No fabricated zero read: the independent genesis evidence
                # must establish absence separately before the gate can pass.
                continue
            headers = [call(chain, 'eth_getBlockByNumber', [hex(n), False]) for n in (block, block + 1)]
            if chain not in identities:
                identities[chain] = (rpc_quantity(call(chain, 'eth_chainId', [])),
                                     rpc_quantity(call(chain, 'eth_blockNumber', [])))
            chain_id, head = identities[chain]
            check_boundary(day, block, headers, chain.value, chain_id, head)
            token = USDS_BY_CHAIN[chain]
            raw = call(chain, 'eth_call', [{'to': token.address.hex,
                       'data': '0x70a08231' + holder.hex[2:].zfill(64)},
                       {'blockHash': headers[0]['hash'], 'requireCanonical': True}])
            require(isinstance(raw, str) and re.fullmatch(r'0x[0-9a-fA-F]{64}', raw) is not None,
                    f'Missing/malformed archive balance: {day}/{chain.value}')
            try:
                balance = int(raw, 16)
            except ValueError as exc:
                raise AssetControlError('Malformed archive balance') from exc
            rows.append({'day': day, 'chain': chain.value, 'holder': holder.hex.lower(),
                         'token': token.address.hex.lower(), 'block': block,
                         'raw_balance': str(balance), 'decimals': token.decimals,
                         'headers': headers, 'chain_id': chain_id, 'head': head})
    return {'schema': 1, 'source': 'independent-archive-usds', 'prime': prime.id, 'states': rows}


def check_wallet_states(prime, observations, control, evidence):
    """Every ALM wallet's deduction must come from a matching raw state read."""
    inventory = control['inventory']['accounts']
    last_day = date.fromisoformat(observations[-1]['day'])
    expected = {(c.value, h.hex.lower()): USDS_BY_CHAIN[c] for c, h in prime.alm.items()
                if c in USDS_BY_CHAIN and prime.alm_start_dates.get(c, date.min) <= last_day}
    accounts = {}
    for key, account in inventory.items():
        if 'alm_usds' in account['deduction_fields']:
            require(account['deduction_fields'] == ['alm_usds'],
                    'A direct native-USDS wallet may claim only the ALM exemption')
            identity = (account['chain'], account['holder'].lower())
            require(identity in expected and identity not in accounts,
                    'ALM deduction must have one native-USDS account per configured ALM wallet')
            require(account['custody_id'].lower() == expected[identity].address.hex.lower(),
                    'ALM deduction custody must be the native USDS token')
            accounts[identity] = key
    require(set(accounts) == set(expected), 'Missing independently observed native-USDS ALM wallet')
    from decimal import Decimal
    for observation in observations:
        for identity, key in accounts.items():
            state = observation['assets'][key]
            if state['block'] == 0:
                from settle.controls.boundaries import check_pre_genesis
                check_pre_genesis(control, observation, evidence)
                continue
            matches = []
            for ref in state['evidence']:
                source = evidence[ref]
                if not isinstance(source, dict) or source.get('source') != 'independent-archive-usds':
                    continue
                require(source['prime'] == prime.id and source['schema'] == 1, 'Wrong wallet evidence identity')
                matches += [r for r in source['states'] if r['day'] == observation['day']
                            and (r['chain'], r['holder'].lower()) == identity]
            require(len(matches) == 1, 'Missing/duplicate independent wallet observation')
            raw = matches[0]
            token = expected[identity]
            require(raw['token'].lower() == token.address.hex.lower()
                    and raw['decimals'] == token.decimals and raw['block'] == state['block'],
                    'Wallet evidence token, decimals or block does not match')
            require(isinstance(raw['raw_balance'], str) and re.fullmatch(r'[0-9]+', raw['raw_balance']) is not None, 'Invalid raw wallet balance')
            require(int(raw['raw_balance']) < 2**256, 'Invalid uint256 balance')
            check_boundary(observation['day'], raw['block'], raw['headers'],
                           identity[0], raw['chain_id'], raw['head'])
            value = Decimal(usds_amount(int(raw['raw_balance'])))
            start = prime.alm_start_dates.get(token.chain, date.min)
            eligible = value if date.fromisoformat(observation['day']) >= start else Decimal(0)
            require(Decimal(state['value_usd']) == value
                    and Decimal(state['deductions']['alm_usds']) == eligible,
                    f'Stale wallet value/deduction on {observation["day"]}/{identity[0]}')
