from dataclasses import replace
from datetime import date
from decimal import Decimal as D

from settle.compute.allocation_capital import replay_history
from settle.domain.config import load_prime_by_id
from settle.domain.pricing import PricingCategory
from settle.domain.primes import Address, Chain, PrincipalReturnOverride, Token, Venue
from settle.extract.hypersync import LogRow
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_capital import AssetMovement as M
from settle.normalize.allocation_capital import CapitalBatch as B
from settle.normalize.allocation_capital import CapitalHistory
from settle.normalize.allocation_custody import (
    ANCHORAGE_RETURN,
    USDC,
    link_buidl_claims,
    link_facility,
)

DAY = date(2026, 8, 1)
CHAIN = Chain.ETHEREUM


def row(block, token, sender, recipient, amount, tx):
    return LogRow(block, 0, 1785542400 + block, token, TRANSFER_TOPIC0,
                  '0x' + sender[2:].rjust(64, '0'), '0x' + recipient[2:].rjust(64, '0'),
                  None, '0x' + f'{int(D(amount) * 10**6):064x}', tx)


def batch(block, tx, movements, minted=D(0)):
    return B('ethereum:' + tx, DAY, 1785542400 + block, 'ethereum', block,
             tuple(movements), minted, minted_by_ilk={'A': minted} if minted else {})


def test_anchorage_round_trip_preserves_borrowed_funding():
    prime = load_prime_by_id('spark')
    holder = prime.alm[CHAIN].hex
    cp = '0x49506c3aa028693458d6ee816b2ec28522946872'
    cash = f'ethereum:{holder}:{USDC}'
    tx = '0x' + '1' * 64
    rows = [row(2, USDC, holder, cp, '5000000', tx),
            row(3, USDC, cp, holder, '5000000', ANCHORAGE_RETURN)]
    bs = [batch(1, '0x' + '0' * 64, [M(cash, D(0), D(5000000))], D(5000000)),
          batch(2, tx, [M(cash, D(5000000), D(-5000000))]),
          batch(3, ANCHORAGE_RETURN, [M(cash, D(0), D(5000000), D(5000000))])]
    accounts, unsupported = {}, {'S23': 'unsupported'}
    token = Token(CHAIN, Address.from_str(USDC), 'USDC', 6)
    mapping = {(USDC, holder): Venue('cash', CHAIN, token, PricingCategory.PAR_STABLE)}
    linked = link_facility(prime, CHAIN, bs, rows, accounts, unsupported, mapping)
    replay = replay_history(CapitalHistory(tuple(linked), accounts, unsupported), DAY, DAY)
    assert replay.ledger.account(cash).borrowed == 5000000
    assert replay.ledger.account(accounts['S23']).borrowed == 0
    assert not replay.unmatched_receipts and not replay.unmatched_outflows
    assert replay.daily_by_ilk[DAY][cash] == {'A': D(5000000)}


def test_buidl_pending_claim_carries_funding_and_realizes_exit_fee():
    prime = load_prime_by_id('grove')
    holder = prime.alm[CHAIN].hex
    venue = next(v for v in prime.venues if v.id == 'E10')
    shares = f'ethereum:{holder}:{venue.token.address.hex}'
    cash = f'ethereum:{holder}:{USDC}'
    req, paid = '0x' + 'a' * 64, '0x' + 'b' * 64
    rows = [row(2, venue.token.address.hex, holder, '0x8780dd016171b91e4df47075da0a947959c34200', '1000', req),
            row(3, USDC, '0xcfc0f98f30742b6d880f90155d4ebb885e55ab33', holder, '999.5', paid)]
    bs = [batch(1, '0x' + '0' * 64, [M(shares, D(0), D(1000))], D(1000)),
          batch(2, req, [M(shares, D(1000), D(-1000))]),
          batch(3, paid, [M(cash, D(0), D('999.5'))])]
    custody = {}
    pending = link_buidl_claims(prime, CHAIN, 2, bs[:2], rows[:1], custody)
    replay = replay_history(CapitalHistory(tuple(pending), {'E10': shares}, {}, custody), DAY, DAY)
    assert sum(replay.ledger.account(a).borrowed for a in custody['E10']) == 1000
    assert not replay.unmatched_outflows
    custody = {}
    linked = link_buidl_claims(prime, CHAIN, 3, bs, rows, custody)
    replay = replay_history(CapitalHistory(tuple(linked), {'E10': shares}, {}, custody), DAY, DAY)
    assert replay.ledger.account(cash).borrowed == D('999.5')
    assert replay.ledger.realised_principal_loss == D('.5')
    assert not replay.unmatched_receipts and not replay.unmatched_outflows


def test_facility_partial_return_releases_principal_and_retains_earned_interest():
    prime = load_prime_by_id('spark')
    holder = prime.alm[CHAIN].hex
    cp = Address.from_str('0x49506c3aa028693458d6ee816b2ec28522946872')
    prime = replace(prime, principal_return_overrides={CHAIN: {cp: [
        PrincipalReturnOverride(DAY, D(10), 'USDC', capital_amount=D(0))]}})
    cash = f'ethereum:{holder}:{USDC}'
    sent, received = '0x' + 'c' * 64, '0x' + 'd' * 64
    # Day-net $10 income: $100 funding followed by $110 return. The claim
    # releases only the originally funded $100; $10 never becomes debt basis.
    logs = [row(2, USDC, holder, cp.hex, '100', sent),
            row(3, USDC, cp.hex, holder, '110', received)]
    batches = [batch(1, '0x' + 'e' * 64, [M(cash, D(0), D(100))], D(100)),
               batch(2, sent, [M(cash, D(100), D(-100))]),
               batch(3, received, [M(cash, D(0), D(110), D(10))])]
    token = Token(CHAIN, Address.from_str(USDC), 'USDC', 6)
    mapping = {(USDC, holder): Venue('cash', CHAIN, token, PricingCategory.PAR_STABLE)}
    accounts, unsupported = {}, {}
    linked = link_facility(prime, CHAIN, batches, logs, accounts, unsupported, mapping)
    replay = replay_history(CapitalHistory(tuple(linked), accounts, unsupported), DAY, DAY)
    assert replay.ledger.account(cash).borrowed == 100
    assert replay.ledger.account(cash).value == 110
    assert replay.ledger.account(accounts['S23']).borrowed == 0
    assert not replay.unmatched_receipts and not replay.unmatched_outflows
