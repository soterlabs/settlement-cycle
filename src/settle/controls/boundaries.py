"""Explicit, source-backed absence of a chain before its first block."""
import re
from datetime import UTC, date, datetime, time

from settle.controls.assets import amount, require
from settle.extract import rpc
from settle.revenue.balance_sheet_sources import CHAIN_IDS


def rpc_quantity(raw):
    require(isinstance(raw, str) and re.fullmatch(r'0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)', raw) is not None,
            'Malformed RPC quantity evidence')
    return int(raw, 16)


def block_hash(raw):
    require(isinstance(raw, str) and re.fullmatch(r'0x[0-9a-fA-F]{64}', raw) is not None,
            'Malformed block hash evidence')
    return raw


def capture_genesis(prime, daily_pins, *, call=None):
    if call is None:
        def call(chain, method, params):
            from settle.domain.primes import Chain
            return rpc._post(rpc.rpc_url(Chain(chain)), method, params)
    chains = {}
    for chain in sorted({c for pins in daily_pins.values() for c, block in pins.items() if block == 0}):
        require(chain in CHAIN_IDS, f'Unsupported chain identity for genesis proof: {chain}')
        chain_id = rpc_quantity(call(chain, 'eth_chainId', []))
        require(chain_id == CHAIN_IDS[chain], 'Wrong chain for genesis proof')
        headers = [call(chain, 'eth_getBlockByNumber', [hex(n), False]) for n in (0, 1)]
        require(all(headers), 'Incomplete genesis header evidence')
        chains[chain] = {'chain_id': chain_id, 'headers': [
            {k: header[k] for k in ('number', 'hash', 'parentHash', 'timestamp') if k in header}
            for header in headers]}
    result = {'schema': 1, 'source': 'independent-chain-genesis', 'prime': prime.id, 'chains': chains}
    for day, pins in daily_pins.items():
        observation = {'day': day, 'pins': pins, 'pre_genesis': {
            chain: 'genesis.json' for chain, pin in pins.items() if pin == 0}}
        check_pre_genesis({'prime': prime.id, 'inventory': {'accounts': {}}}, observation,
                          {'genesis.json': result})
    return result


def check_pre_genesis(control, observation, evidence):
    zero = {chain for chain, block in observation['pins'].items() if block == 0}
    references = observation.get('pre_genesis', {})
    require(set(references) == zero, 'Zero block pins require explicit pre-genesis evidence')
    for chain, relative in references.items():
        require(relative in evidence, 'Missing retained pre-genesis evidence')
        source = evidence[relative]
        require(source['schema'] == 1 and source['source'] == 'independent-chain-genesis'
                and source['prime'] == control['prime'], 'Wrong pre-genesis evidence identity')
        proof = source['chains'][chain]
        require(chain in CHAIN_IDS and type(proof['chain_id']) is int and proof['chain_id'] == CHAIN_IDS[chain], 'Wrong genesis chain')
        genesis, first = proof['headers']
        boundary = int(datetime.combine(date.fromisoformat(observation['day']), time.max, UTC).timestamp())
        require(rpc_quantity(genesis['number']) == 0 and rpc_quantity(first['number']) == 1
                and block_hash(first['parentHash']) == block_hash(genesis['hash'])
                and block_hash(first['hash'])
                and rpc_quantity(first['timestamp']) > boundary, 'Chain was already live at this close')
        for key, account in control['inventory']['accounts'].items():
            if account['chain'] == chain:
                state = observation['assets'][key]
                require(relative in state['evidence'], 'Pre-genesis position lacks its absence evidence')
                require(amount(state['value_usd']) == 0
                        and all(amount(v) == 0 for v in state['deductions'].values()),
                        'Pre-genesis holdings and deductions must be zero')
