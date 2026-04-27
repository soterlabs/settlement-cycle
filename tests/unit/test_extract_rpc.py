"""Unit tests for `settle.extract.rpc` (RPC encoding helpers, no live network)."""

from __future__ import annotations

import pytest

from settle.domain.primes import Address, Chain
from settle.extract.rpc import _pad_address, _pad_uint, rpc_url


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
