from decimal import Decimal as D

import pytest

from settle.domain.config import load_prime_by_id
from settle.extract._keccak import keccak256
from settle.normalize.async_vaults import async_vault_exposure


def target(prime, vid):
    p = load_prime_by_id(prime)
    return p, next(v for v in p.venues if v.id == vid)


def install(
    monkeypatch, prime, venue, *, queued=0, manual=0, cooldown=0, expiry=1, implementation=None
):
    from settle.domain import Address
    from settle.extract import rpc

    def encode(*args):
        return "0x" + "".join(format(int(a), "064x") for a in args)

    def selector(text):
        return "0x" + keccak256(text.encode())[:4].hex()

    manager = Address.from_str("0x" + "11" * 20)
    silo = Address.from_str("0x" + "22" * 20)
    owner = (venue.holder_override or prime.alm[venue.chain]).hex[2:].zfill(64)
    monkeypatch.setattr(rpc, "is_contract_deployed", lambda *a: True)

    def call(chain, address, data, block):
        if venue.async_allocation.kind == "maple":
            escrow = venue.async_allocation.managers[0]
            replies = {
                (venue.token.address, selector("asset()")): encode(
                    int(venue.underlying.address.hex, 16)
                ),
                (venue.token.address, selector("manager()")): encode(int(manager.hex, 16)),
                (manager, selector("withdrawalManager()")): encode(int(escrow.address.hex, 16)),
                (escrow.address, selector("implementation()")): encode(
                    int((implementation or escrow.implementations[0]).hex, 16)
                ),
                (escrow.address, selector("pool()")): encode(int(venue.token.address.hex, 16)),
                (escrow.address, selector("userEscrowedShares(address)") + owner): encode(queued),
                (escrow.address, selector("manualSharesAvailable(address)") + owner): encode(
                    manual
                ),
            }
        else:
            replies = {
                (venue.token.address, selector("asset()")): encode(
                    int(venue.underlying.address.hex, 16)
                ),
                (venue.token.address, selector("silo()")): encode(int(silo.hex, 16)),
                (venue.token.address, selector("cooldowns(address)") + owner): encode(
                    expiry, cooldown
                ),
            }
        if (address, data) not in replies:
            raise ValueError("Unexpected ownership selector/controller")
        return replies[address, data]

    monkeypatch.setattr(rpc, "eth_call", call)


@pytest.mark.parametrize("name,vid", [("spark", "S14"), ("spark", "S15"), ("obex", "V1")])
def test_maple_includes_queued_and_manual_shares_for_only_this_owner(monkeypatch, name, vid):
    prime, venue = target(name, vid)
    install(monkeypatch, prime, venue, queued=10_000_000, manual=2_000_000)
    exposure = async_vault_exposure(prime, venue, 26093737)
    assert exposure.redemption_shares == 12
    assert exposure.value(D("1.2")) == D("14.4")
    assert exposure.claims[0]["owner"] == prime.alm[venue.chain].hex
    assert exposure.redemption_cash == 0


def test_unknown_maple_upgrade_stops_instead_of_assuming_zero(monkeypatch):
    from settle.domain import Address

    prime, venue = target("obex", "V1")
    install(monkeypatch, prime, venue, implementation=Address.from_str("0x" + "99" * 20))
    with pytest.raises(RuntimeError, match="exposure read failed"):
        async_vault_exposure(prime, venue, 26093737)


def test_expired_ethena_cooldown_still_owns_fixed_underlying(monkeypatch):
    prime, venue = target("spark", "S16")
    install(monkeypatch, prime, venue, cooldown=123 * 10**18, expiry=1)
    exposure = async_vault_exposure(prime, venue, 26093737)
    assert exposure.redemption_shares == 0
    assert exposure.redemption_cash == 123
    assert exposure.value(D("1.08")) == exposure.value(D("5")) == 123
    assert exposure.claims[0]["asset"] == venue.underlying.address.hex


def test_nonparticipating_grove_venues_are_not_enabled():
    assert all(v.async_allocation is None for v in load_prime_by_id("grove").venues)


def test_router_is_not_misidentified_as_a_maple_escrow():
    prime, venue = target("spark", "S14")
    escrow_addresses = {m.address for m in venue.async_allocation.managers}
    assert prime.alm[venue.chain] not in escrow_addresses
    from settle.domain import Address

    assert Address.from_str("0x491edfb0b8b608044e227225c715981a30f3a44e") not in escrow_addresses
    assert Address.from_str("0x7ad5ffa5fdf509e30186f4609c2f6269f4b6158f") not in escrow_addresses
