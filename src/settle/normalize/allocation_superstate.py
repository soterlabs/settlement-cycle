"""Capital-only USTB pricing from the oracle used by its subscription contract.

A standalone daily Chainlink NAV is not the same as this execution-time NAV.
The token's real-time oracle interpolates/extrapolates its checkpoints and
rejects expired checkpoints itself. Do not change published report pricing.
https://github.com/superstateinc/ustb/blob/78e8ca22a319efd265e7d6ba2c326475cb6b6e2e/src/SuperstateToken.sol
"""
from decimal import Decimal

from ..domain.primes import Address, Chain
from ..extract import hypersync, rpc
from ..extract._keccak import keccak256

USTB = '0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e'


def ustb_capital_price(token, block):
    if token.chain != Chain.ETHEREUM or token.address.hex != USTB:
        return None

    def read(address, signature, words):
        data = rpc.eth_call(Chain.ETHEREUM, Address.from_str(address),
                            '0x' + keccak256(signature.encode())[:4].hex(), block)
        if len(data) != 2 + 64 * words:
            raise ValueError('Invalid USTB capital oracle response')
        return [int(data[i:i+64], 16) for i in range(2, len(data), 64)]

    address = read(USTB, 'superstateOracle()', 1)[0]
    if not 0 < address < 2**160:
        raise ValueError('USTB capital oracle is unavailable')
    oracle = '0x' + f'{address:040x}'
    decimals = read(oracle, 'decimals()', 1)[0]
    delay = read(USTB, 'maximumOracleDelay()', 1)[0]
    round_id, answer, started, updated, answered = read(oracle, 'latestRoundData()', 5)
    stamp = hypersync.block_timestamp(Chain.ETHEREUM.value, block)
    if (decimals != 6 or not delay or not round_id or not 0 < answer < 2**255
            or answered < round_id or not 0 < started <= updated <= stamp or stamp - updated > delay):
        raise ValueError('Invalid or stale USTB capital NAV')
    return Decimal(answer) / Decimal(10**decimals)
