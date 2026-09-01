"""Unit tests for _check_centrifuge_in_flight in settle.compute.monthly_pnl.

The function runs as step 2h of the orchestrator immediately after pin blocks
are resolved.  For each venue that has a ``centrifuge_vault`` configured it
queries four ERC-7540 view functions at both the SoM and EoM pin blocks and
logs a caps WARNING if any return a non-zero value.

All tests monkeypatch ``settle.extract.rpc.eth_call`` and
``settle.extract.rpc.is_contract_deployed`` so no network calls are made.
``tmp_cache_dir`` is included so the cache decorator does not interfere with
the monkeypatched function objects.
"""

from __future__ import annotations

import logging
from datetime import date

import pytest

from settle.compute.monthly_pnl import _check_centrifuge_in_flight
from settle.domain import Address, Chain, PricingCategory, Token, Venue
from settle.domain.primes import Prime

# ── test fixtures ─────────────────────────────────────────────────────────────

_VAULT = Address.from_str("0x" + "cc" * 20)
_ALM   = Address.from_str("0x" + "dd" * 20)
_TOKEN = Address.from_str("0x" + "ee" * 20)

_SOM_BLOCK = 24_000_000
_EOM_BLOCK = 24_500_000

_PIN_SOM = {Chain.ETHEREUM: _SOM_BLOCK}
_PIN_EOM = {Chain.ETHEREUM: _EOM_BLOCK}

# 32-byte zero uint256 (ERC-7540 "no pending shares")
_ZERO    = "0x" + "0" * 64
# 32-byte uint256 = 1 (any non-zero → in-flight)
_NONZERO = "0x" + "0" * 63 + "1"


def _centrifuge_venue(*, skip: bool = False) -> Venue:
    return Venue(
        id="E8",
        chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, _TOKEN, "JAAA", 6),
        pricing_category=PricingCategory.RWA_TRANCHE,
        centrifuge_vault=_VAULT,
        skip=skip,
        label="JAAA",
    )


def _plain_venue() -> Venue:
    """Venue without centrifuge_vault — should never be queried."""
    return Venue(
        id="V1",
        chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, _TOKEN, "USDC", 6),
        pricing_category=PricingCategory.ERC4626_VAULT,
        label="No Vault",
    )


def _prime(venues: list[Venue]) -> Prime:
    return Prime(
        id="grove",
        ilk_bytes32=b"\x00" * 32,
        start_date=date(2024, 1, 1),
        alm={Chain.ETHEREUM: _ALM},
        venues=venues,
    )


# ── helpers ───────────────────────────────────────────────────────────────────

def _patch_rpc(monkeypatch, *, deployed: bool = True, eth_call_return: str = _ZERO):
    """Monkeypatch both rpc helpers with simple stubs."""
    import settle.extract.rpc as _rpc
    monkeypatch.setattr(_rpc, "is_contract_deployed", lambda *a: deployed)
    monkeypatch.setattr(_rpc, "eth_call", lambda *a, **kw: eth_call_return)


def _in_flight_warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "IN-FLIGHT" in r.getMessage()]


# ── tests ─────────────────────────────────────────────────────────────────────

def test_venue_without_centrifuge_vault_never_queried(monkeypatch, tmp_cache_dir):
    """Venues that have no centrifuge_vault must not trigger any eth_call."""
    import settle.extract.rpc as _rpc

    def _must_not_call(*_a, **_kw):
        raise AssertionError("eth_call must not be called for non-Centrifuge venue")

    monkeypatch.setattr(_rpc, "is_contract_deployed", lambda *a: True)
    monkeypatch.setattr(_rpc, "eth_call", _must_not_call)

    _check_centrifuge_in_flight(_prime([_plain_venue()]), _PIN_SOM, _PIN_EOM)


def test_skipped_venue_not_queried(monkeypatch, tmp_cache_dir):
    """venue.skip=True → centrifuge_vault not checked even if set."""
    import settle.extract.rpc as _rpc

    def _must_not_call(*_a, **_kw):
        raise AssertionError("eth_call must not be called for skipped venue")

    monkeypatch.setattr(_rpc, "is_contract_deployed", lambda *a: True)
    monkeypatch.setattr(_rpc, "eth_call", _must_not_call)

    _check_centrifuge_in_flight(
        _prime([_centrifuge_venue(skip=True)]), _PIN_SOM, _PIN_EOM,
    )


def test_contract_not_deployed_skips_eth_calls(monkeypatch, tmp_cache_dir, caplog):
    """is_contract_deployed → False → no eth_call and no WARNING emitted."""
    import settle.extract.rpc as _rpc

    def _must_not_call(*_a, **_kw):
        raise AssertionError("eth_call must not be called when contract not deployed")

    monkeypatch.setattr(_rpc, "is_contract_deployed", lambda *a: False)
    monkeypatch.setattr(_rpc, "eth_call", _must_not_call)

    with caplog.at_level(logging.WARNING, logger="settle.compute.monthly_pnl"):
        _check_centrifuge_in_flight(_prime([_centrifuge_venue()]), _PIN_SOM, _PIN_EOM)

    assert not _in_flight_warnings(caplog)


def test_all_zero_responses_produce_no_warning(monkeypatch, tmp_cache_dir, caplog):
    """All 4 ERC-7540 selectors return 0 at both blocks → no WARNING logged."""
    _patch_rpc(monkeypatch, deployed=True, eth_call_return=_ZERO)

    with caplog.at_level(logging.WARNING, logger="settle.compute.monthly_pnl"):
        _check_centrifuge_in_flight(_prime([_centrifuge_venue()]), _PIN_SOM, _PIN_EOM)

    assert not _in_flight_warnings(caplog)


def test_pending_redeem_at_som_logs_warning(monkeypatch, tmp_cache_dir, caplog):
    """pendingRedeemRequest > 0 at SoM → one WARNING naming the venue, kind, and block."""
    import settle.extract.rpc as _rpc
    from settle.extract.rpc import SEL_PENDING_REDEEM_REQUEST

    monkeypatch.setattr(_rpc, "is_contract_deployed", lambda *a: True)

    def _selective(chain, contract, data, block):
        if data.startswith(SEL_PENDING_REDEEM_REQUEST) and block == _SOM_BLOCK:
            return "0x" + f"{318_545_940_695_567:064x}"
        return _ZERO

    monkeypatch.setattr(_rpc, "eth_call", _selective)

    with caplog.at_level(logging.WARNING, logger="settle.compute.monthly_pnl"):
        _check_centrifuge_in_flight(_prime([_centrifuge_venue()]), _PIN_SOM, _PIN_EOM)

    warnings = _in_flight_warnings(caplog)
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "PENDING REDEEM" in msg
    assert "E8" in msg
    assert "SoM" in msg


def test_claimable_deposit_at_eom_logs_warning(monkeypatch, tmp_cache_dir, caplog):
    """claimableDepositRequest > 0 at EoM → one WARNING naming kind and block."""
    import settle.extract.rpc as _rpc
    from settle.extract.rpc import SEL_CLAIMABLE_DEPOSIT_REQUEST

    monkeypatch.setattr(_rpc, "is_contract_deployed", lambda *a: True)

    def _selective(chain, contract, data, block):
        if data.startswith(SEL_CLAIMABLE_DEPOSIT_REQUEST) and block == _EOM_BLOCK:
            return _NONZERO
        return _ZERO

    monkeypatch.setattr(_rpc, "eth_call", _selective)

    with caplog.at_level(logging.WARNING, logger="settle.compute.monthly_pnl"):
        _check_centrifuge_in_flight(_prime([_centrifuge_venue()]), _PIN_SOM, _PIN_EOM)

    warnings = _in_flight_warnings(caplog)
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "CLAIMABLE DEPOSIT" in msg
    assert "EoM" in msg


def test_all_four_selectors_queried_per_block(monkeypatch, tmp_cache_dir):
    """All 4 ERC-7540 selectors are called at each block (8 calls total for one venue)."""
    import settle.extract.rpc as _rpc
    from settle.extract.rpc import (
        SEL_PENDING_DEPOSIT_REQUEST,
        SEL_CLAIMABLE_DEPOSIT_REQUEST,
        SEL_PENDING_REDEEM_REQUEST,
        SEL_CLAIMABLE_REDEEM_REQUEST,
    )

    called_selectors: set[tuple[str, int]] = set()

    def _record(chain, contract, data, block):
        # Extract the 4-byte selector prefix from calldata
        called_selectors.add((data[:10], block))
        return _ZERO

    monkeypatch.setattr(_rpc, "is_contract_deployed", lambda *a: True)
    monkeypatch.setattr(_rpc, "eth_call", _record)

    _check_centrifuge_in_flight(_prime([_centrifuge_venue()]), _PIN_SOM, _PIN_EOM)

    expected = {
        (SEL_PENDING_DEPOSIT_REQUEST,   _SOM_BLOCK),
        (SEL_CLAIMABLE_DEPOSIT_REQUEST, _SOM_BLOCK),
        (SEL_PENDING_REDEEM_REQUEST,    _SOM_BLOCK),
        (SEL_CLAIMABLE_REDEEM_REQUEST,  _SOM_BLOCK),
        (SEL_PENDING_DEPOSIT_REQUEST,   _EOM_BLOCK),
        (SEL_CLAIMABLE_DEPOSIT_REQUEST, _EOM_BLOCK),
        (SEL_PENDING_REDEEM_REQUEST,    _EOM_BLOCK),
        (SEL_CLAIMABLE_REDEEM_REQUEST,  _EOM_BLOCK),
    }
    assert called_selectors == expected


def test_all_nonzero_produces_eight_warnings(monkeypatch, tmp_cache_dir, caplog):
    """All 4 selectors non-zero at both blocks → 8 separate IN-FLIGHT WARNINGs."""
    _patch_rpc(monkeypatch, deployed=True, eth_call_return=_NONZERO)

    with caplog.at_level(logging.WARNING, logger="settle.compute.monthly_pnl"):
        _check_centrifuge_in_flight(_prime([_centrifuge_venue()]), _PIN_SOM, _PIN_EOM)

    assert len(_in_flight_warnings(caplog)) == 8   # 4 selectors × 2 blocks


def test_holder_override_used_when_set(monkeypatch, tmp_cache_dir):
    """When venue.holder_override is set, it is used instead of prime.alm."""
    import settle.extract.rpc as _rpc

    override_addr = Address.from_str("0x" + "ff" * 20)
    venue = Venue(
        id="E8",
        chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, _TOKEN, "JAAA", 6),
        pricing_category=PricingCategory.RWA_TRANCHE,
        centrifuge_vault=_VAULT,
        holder_override=override_addr,
        label="JAAA override",
    )

    used_datas: list[str] = []

    def _record(chain, contract, data, block):
        used_datas.append(data)
        return _ZERO

    monkeypatch.setattr(_rpc, "is_contract_deployed", lambda *a: True)
    monkeypatch.setattr(_rpc, "eth_call", _record)

    _check_centrifuge_in_flight(_prime([venue]), _PIN_SOM, _PIN_EOM)

    # holder address must appear in the calldata (last 20 bytes of the 32-byte address arg)
    override_hex = override_addr.value.hex()
    assert all(override_hex in d for d in used_datas), (
        "holder_override address not found in calldata"
    )
    alm_hex = _ALM.value.hex()
    assert not any(alm_hex in d for d in used_datas), (
        "prime.alm address should NOT appear when holder_override is set"
    )


def test_multiple_centrifuge_venues_each_checked_independently(monkeypatch, tmp_cache_dir, caplog):
    """Two Centrifuge venues with different in-flight states → each emits its
    own per-venue WARNING.  Catches an off-by-one bug where the iteration
    short-circuits after the first venue (e.g. a misplaced ``return``)."""
    import settle.extract.rpc as _rpc
    from settle.extract.rpc import SEL_PENDING_REDEEM_REQUEST

    vault_a = Address.from_str("0x" + "a1" * 20)
    vault_b = Address.from_str("0x" + "b2" * 20)
    venue_a = Venue(
        id="E8", chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, _TOKEN, "JAAA", 6),
        pricing_category=PricingCategory.RWA_TRANCHE,
        centrifuge_vault=vault_a, label="JAAA",
    )
    venue_b = Venue(
        id="E9", chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, _TOKEN, "JTRSY", 6),
        pricing_category=PricingCategory.RWA_TRANCHE,
        centrifuge_vault=vault_b, label="JTRSY",
    )

    monkeypatch.setattr(_rpc, "is_contract_deployed", lambda *a: True)

    def _selective(chain, contract, data, block):
        # Only venue B has a pending redeem; venue A is clean.
        if (contract.value == vault_b.value
                and data.startswith(SEL_PENDING_REDEEM_REQUEST)):
            return _NONZERO
        return _ZERO

    monkeypatch.setattr(_rpc, "eth_call", _selective)

    with caplog.at_level(logging.WARNING, logger="settle.compute.monthly_pnl"):
        _check_centrifuge_in_flight(_prime([venue_a, venue_b]), _PIN_SOM, _PIN_EOM)

    warnings = _in_flight_warnings(caplog)
    # E9 has PENDING REDEEM at both SoM and EoM (constant non-zero per selector)
    assert len(warnings) == 2
    assert all("E9" in w.getMessage() for w in warnings)
    assert all("PENDING REDEEM" in w.getMessage() for w in warnings)


def test_chain_missing_from_pin_blocks_is_skipped(monkeypatch, tmp_cache_dir):
    """Venue whose chain isn't in the resolved pin_blocks → silently skipped
    (no eth_call, no error).  Mirrors a partial-resolution scenario where one
    chain failed but others succeeded — we don't want a missing pin block to
    crash the entire pipeline."""
    import settle.extract.rpc as _rpc

    def _must_not_call(*_a, **_kw):
        raise AssertionError("eth_call must not run when chain has no pin block")

    monkeypatch.setattr(_rpc, "is_contract_deployed", lambda *a: True)
    monkeypatch.setattr(_rpc, "eth_call", _must_not_call)

    # Pin blocks missing for ethereum on the EoM side
    _check_centrifuge_in_flight(
        _prime([_centrifuge_venue()]),
        pin_blocks_som=_PIN_SOM,
        pin_blocks_eom={},                # ← deliberately empty
    )


def test_no_alm_for_chain_is_skipped(monkeypatch, tmp_cache_dir):
    """Venue on a chain where prime.alm has no entry AND no holder_override
    → silently skipped (no eth_call, no error)."""
    import settle.extract.rpc as _rpc

    def _must_not_call(*_a, **_kw):
        raise AssertionError("eth_call must not run when no holder resolvable")

    monkeypatch.setattr(_rpc, "is_contract_deployed", lambda *a: True)
    monkeypatch.setattr(_rpc, "eth_call", _must_not_call)

    prime_no_alm = Prime(
        id="grove",
        ilk_bytes32=b"\x00" * 32,
        start_date=date(2024, 1, 1),
        alm={},                           # ← no ALM at all
        venues=[_centrifuge_venue()],
    )
    _check_centrifuge_in_flight(prime_no_alm, _PIN_SOM, _PIN_EOM)


# ── valuation: escrowed shares are added back to the position ─────────────────
#
# The checks above only WARN. These cover the fix that makes the number
# right: `_centrifuge_in_flight_shares` tops the escrowed shares back into
# `get_position_value`, so `eom − som − inflow` isolates yield instead of
# booking the escrow transfer as a phantom loss (Grove E9, Aug 2026:
# −$22,492,398.88 on a $0 event-sourced inflow).

def test_in_flight_shares_added_to_balance(monkeypatch, tmp_cache_dir):
    """Pending + claimable redeem shares both count toward the position."""
    from decimal import Decimal
    from settle.normalize.positions import _centrifuge_in_flight_shares
    import settle.extract.rpc as _rpc
    from settle.extract.rpc import (
        SEL_PENDING_REDEEM_REQUEST, SEL_CLAIMABLE_REDEEM_REQUEST,
    )

    # 6-decimal token: 1,500,000 pending + 500,000 claimable = 2,000,000 shares
    def _fake_call(chain, contract, data, block):
        if data.startswith(SEL_PENDING_REDEEM_REQUEST):
            return "0x" + format(1_500_000 * 10**6, "064x")
        if data.startswith(SEL_CLAIMABLE_REDEEM_REQUEST):
            return "0x" + format(500_000 * 10**6, "064x")
        return _ZERO

    monkeypatch.setattr(_rpc, "is_contract_deployed", lambda *a: True)
    monkeypatch.setattr(_rpc, "eth_call", _fake_call)

    venue = _centrifuge_venue()
    prime = _prime([venue])
    got = _centrifuge_in_flight_shares(prime, venue, _EOM_BLOCK)
    assert got == Decimal("2000000"), got


def test_no_in_flight_means_no_adjustment(monkeypatch, tmp_cache_dir):
    """All-zero reads must leave the balance untouched (not raise, not guess)."""
    from decimal import Decimal
    from settle.normalize.positions import _centrifuge_in_flight_shares

    _patch_rpc(monkeypatch, eth_call_return=_ZERO)
    venue = _centrifuge_venue()
    assert _centrifuge_in_flight_shares(_prime([venue]), venue, _EOM_BLOCK) == Decimal("0")


def test_non_centrifuge_venue_gets_no_adjustment(monkeypatch, tmp_cache_dir):
    """A venue without centrifuge_vault must never be topped up."""
    from decimal import Decimal
    from settle.normalize.positions import _centrifuge_in_flight_shares

    _patch_rpc(monkeypatch, eth_call_return=_NONZERO)
    venue = _plain_venue()
    assert _centrifuge_in_flight_shares(_prime([venue]), venue, _EOM_BLOCK) == Decimal("0")


def test_failed_read_raises_rather_than_booking_a_phantom(monkeypatch, tmp_cache_dir):
    """A read failure on a DEPLOYED vault must stop the run, not return 0.

    Degrading to 0 here is indistinguishable from "no in-flight redemption",
    which silently re-books the phantom loss into a published settlement.
    """
    import pytest as _pytest
    from settle.normalize.positions import _centrifuge_in_flight_shares
    import settle.extract.rpc as _rpc

    def _boom(*a, **kw):
        raise RuntimeError("rpc down")

    monkeypatch.setattr(_rpc, "is_contract_deployed", lambda *a: True)
    monkeypatch.setattr(_rpc, "eth_call", _boom)
    venue = _centrifuge_venue()
    with _pytest.raises(RuntimeError, match="in-flight"):
        _centrifuge_in_flight_shares(_prime([venue]), venue, _EOM_BLOCK)


def test_undeployed_vault_still_degrades_quietly(monkeypatch, tmp_cache_dir):
    """Not-yet-deployed is a real state (venue onboarded mid-period), not an
    outage — it must return 0 without raising."""
    from decimal import Decimal
    from settle.normalize.positions import _centrifuge_in_flight_shares

    _patch_rpc(monkeypatch, deployed=False, eth_call_return=_NONZERO)
    venue = _centrifuge_venue()
    assert _centrifuge_in_flight_shares(_prime([venue]), venue, _EOM_BLOCK) == Decimal("0")
