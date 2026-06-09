"""Unit tests for `settle.extract.rpc` (RPC encoding helpers, no live network)."""

from __future__ import annotations

import pytest

from settle.domain.primes import Address, Chain
from settle.extract import rpc as _rpc
from settle.extract.rpc import _pad_address, _pad_uint, ilk_rate, rpc_url


def test_pad_address_pads_to_32_bytes():
    a = Address.from_str("0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2")
    padded = _pad_address(a)
    assert len(padded) == 64
    assert padded.endswith("b6dd7ae22c9922afee0642f9ac13e58633f715a2")
    assert padded.startswith("000000000000000000000000")


def test_pad_uint_pads_to_32_bytes():
    assert _pad_uint(0) == "0" * 64
    assert len(_pad_uint(123)) == 64
    assert _pad_uint(123).endswith("7b")  # 123 = 0x7b


def test_pad_uint_rejects_negative():
    with pytest.raises(ValueError):
        _pad_uint(-1)


def test_rpc_url_reads_eth_rpc_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ETH_RPC", "https://example.com")
    assert rpc_url(Chain.ETHEREUM) == "https://example.com"


@pytest.mark.parametrize("chain,env_var", [
    (Chain.ETHEREUM,    "ETH_RPC"),           # alias — NOT ETHEREUM_RPC
    (Chain.BASE,        "BASE_RPC"),
    (Chain.ARBITRUM,    "ARBITRUM_RPC"),
    (Chain.OPTIMISM,    "OPTIMISM_RPC"),
    (Chain.UNICHAIN,    "UNICHAIN_RPC"),
    (Chain.AVALANCHE_C, "AVALANCHE_C_RPC"),
    (Chain.PLUME,       "PLUME_RPC"),
    (Chain.MONAD,       "MONAD_RPC"),
])
def test_rpc_url_chain_to_env_mapping(
    chain: Chain, env_var: str, monkeypatch: pytest.MonkeyPatch,
):
    """Each Chain maps to a deterministic env var. Locked-in test so future
    additions to ``Chain`` don't silently fall through to a wrong default."""
    # Clear ALL RPC env vars so we can test which one this chain actually reads.
    from settle.extract.rpc import RPC_ENV_VARS
    for v in RPC_ENV_VARS.values():
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv(env_var, f"https://{env_var}.example.com")
    assert rpc_url(chain) == f"https://{env_var}.example.com"


def test_rpc_url_raises_when_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ETH_RPC", raising=False)
    with pytest.raises(RuntimeError, match="ETH_RPC"):
        rpc_url(Chain.ETHEREUM)


def test_rpc_url_mapping_covers_every_chain():
    """Every member of ``Chain`` must have an env-var mapping. Locks in the
    invariant — adding a new chain without an entry raises immediately rather
    than silently falling through to a wrong default."""
    from settle.extract.rpc import RPC_ENV_VARS
    missing = [c for c in Chain if c not in RPC_ENV_VARS]
    assert missing == [], f"Chains without RPC env-var mapping: {missing}"


def _ilks_response(art_raw: int, rate_raw: int) -> str:
    """Build a 5-word ABI response for ``ilks(bytes32)`` →
    ``(Art, rate, spot, line, dust)``. Only Art + rate are loaded with
    distinct sentinels; the trailing 3 words are zeros so the test can
    catch any wrong-offset extraction.
    """
    art_hex  = f"{art_raw:064x}"
    rate_hex = f"{rate_raw:064x}"
    zeros    = "0" * 64
    return "0x" + art_hex + rate_hex + zeros + zeros + zeros


def test_ilk_rate_extracts_rate_word_at_offset_64_128(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """``Vat.ilks(ilk)`` returns ``(Art, rate, spot, line, dust)`` —
    five 32-byte words. ``ilk_rate`` must extract the SECOND word (offset
    64–128 in hex chars). Locks in the offset so a wrong slice (e.g.
    [0:64] would return Art-as-rate) is caught immediately."""
    sentinel_art  = 999_999_999          # at [0:64], must NOT be returned
    sentinel_rate = 1_045_000_000_000_000_000_000_000_000   # ≈ 1.045 in ray
    monkeypatch.setattr(
        _rpc, "eth_call",
        lambda chain, contract, data, block: _ilks_response(
            sentinel_art, sentinel_rate,
        ),
    )
    vat = Address.from_str("0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B")
    ilk = bytes.fromhex(
        "414c4c4f4341544f522d535041524b2d41000000000000000000000000000000"
    )
    out = ilk_rate(Chain.ETHEREUM, vat, ilk, 24971074)
    assert out == sentinel_rate, (
        f"expected rate word ({sentinel_rate}), got {out} — "
        "ABI offset for the rate word in ilks(bytes32) is hex chars [64:128]"
    )


def test_ilk_rate_returns_ray_one_on_zero_rate(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """Uninitialised ilks (``rate == 0``) degrade to 1.0 ray so multiplicative
    callers (`Art × rate / RAY`) reduce to Art-only semantics — correct for
    ALLOCATOR-BLOOM-A and any ilk before its first ``jug.drip``."""
    monkeypatch.setattr(
        _rpc, "eth_call",
        lambda *a, **k: _ilks_response(art_raw=12345, rate_raw=0),
    )
    vat = Address.from_str("0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B")
    ilk = b"\x00" * 32
    assert ilk_rate(Chain.ETHEREUM, vat, ilk, 24971074) == 10**27


def test_ilk_rate_returns_ray_one_on_short_response(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """A truncated RPC response (< 128 hex chars after ``0x``) cannot carry
    the rate word; degrade to 1.0 ray rather than crash."""
    monkeypatch.setattr(
        _rpc, "eth_call",
        lambda *a, **k: "0x" + "00" * 31,   # 62 hex chars — too short
    )
    vat = Address.from_str("0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B")
    ilk = b"\x01" * 32
    assert ilk_rate(Chain.ETHEREUM, vat, ilk, 24971074) == 10**27


# ---------------------------------------------------------------------------
# find_block_at_or_before guards (H1: never cache a wrong pin block)
# ---------------------------------------------------------------------------

from datetime import UTC, datetime  # noqa: E402

_ANCHOR = datetime(2026, 3, 31, 23, 59, 59, tzinfo=UTC)
_TARGET = int(_ANCHOR.timestamp())


def _patch_chain(monkeypatch, *, head: int, ts_of_block):
    """Stub latest_block + block_timestamp with a synthetic chain."""
    monkeypatch.setattr(_rpc, "latest_block", lambda chain: head)
    monkeypatch.setattr(_rpc, "block_timestamp", lambda chain, b: ts_of_block(b))


def test_find_block_refuses_anchor_beyond_chain_head(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """Running a month before it has ended must raise, not return (and
    permanently cache) the current chain head as the EoM pin block."""
    monkeypatch.delenv("DUNE_API_KEY", raising=False)
    _patch_chain(monkeypatch, head=100, ts_of_block=lambda b: _TARGET - 1000 + b)
    with pytest.raises(ValueError, match="beyond the chain head"):
        _rpc.find_block_at_or_before(Chain.ETHEREUM, _ANCHOR)


def test_find_block_head_exactly_at_anchor_is_provably_final(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """Timestamps are strictly increasing, so head_ts == target IS the
    at-or-before block — no refusal."""
    monkeypatch.delenv("DUNE_API_KEY", raising=False)
    _patch_chain(monkeypatch, head=100, ts_of_block=lambda b: _TARGET - (100 - b) * 12)
    assert _rpc.find_block_at_or_before(Chain.ETHEREUM, _ANCHOR) == 100


def test_find_block_binary_search_still_resolves_past_anchor(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """Normal case: anchor strictly inside the chain — binary search finds
    the highest block with ts <= target."""
    monkeypatch.delenv("DUNE_API_KEY", raising=False)
    # Block b has ts = _TARGET - 1006 + 12*b → block 83 has ts _TARGET - 10
    # (<= target), block 84 has ts _TARGET + 2 (> target).
    _patch_chain(monkeypatch, head=100, ts_of_block=lambda b: _TARGET - 1006 + 12 * b)
    assert _rpc.find_block_at_or_before(Chain.ETHEREUM, _ANCHOR) == 83


def test_dune_block_verified_rejects_lagging_index(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """evms.blocks behind the anchor: Dune returns its max indexed block,
    whose successor is still <= target — must be rejected."""
    _patch_chain(monkeypatch, head=100, ts_of_block=lambda b: _TARGET - 1006 + 12 * b)
    # Block 50 is well before the anchor; block 51 is also <= target.
    assert _rpc._dune_block_verified(Chain.ETHEREUM, 50, _TARGET, _ANCHOR) is False


def test_dune_block_verified_rejects_dune_index_head(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    """Dune returning >= the RPC head can't be proven to bracket the anchor."""
    _patch_chain(monkeypatch, head=100, ts_of_block=lambda b: _TARGET - 1006 + 12 * b)
    assert _rpc._dune_block_verified(Chain.ETHEREUM, 100, _TARGET, _ANCHOR) is False


def test_dune_block_verified_rejects_block_after_anchor(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    _patch_chain(monkeypatch, head=100, ts_of_block=lambda b: _TARGET - 1006 + 12 * b)
    assert _rpc._dune_block_verified(Chain.ETHEREUM, 90, _TARGET, _ANCHOR) is False


def test_dune_block_verified_accepts_true_bracket(
    tmp_cache_dir, monkeypatch: pytest.MonkeyPatch,
):
    _patch_chain(monkeypatch, head=100, ts_of_block=lambda b: _TARGET - 1006 + 12 * b)
    # Block 83 ts = _TARGET - 10 <= target; block 84 ts = _TARGET + 2 > target.
    assert _rpc._dune_block_verified(Chain.ETHEREUM, 83, _TARGET, _ANCHOR) is True
