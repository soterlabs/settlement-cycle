"""Selector pinning for ``DuneSavingsV2DeployedSource``.

The carve-out source reads ``spUSDC_V2.assetsOutstanding()`` via a
hardcoded 4-byte selector. A typo'd selector would NOT fail loudly —
``eth_call`` on an unknown selector returns empty/reverts, the source
maps that to ``Decimal("0")``, and the consumer's carry-forward treats
it as a pre-deployment vault → the carve-out silently vanishes and the
prime is over-credited by ``SSR × deployed_slice``. Pin the selector to
``keccak256("assetsOutstanding()")[:4]`` so a byte error is a test
failure, not a silent accounting drift.

``pycryptodome`` is an environment extra, not a declared dependency —
skip (don't fail) when it's absent so CI without it stays green.
"""

import pytest

from settle.normalize.sources.dune_savings_v2_deployed import (
    _SEL_ASSETS_OUTSTANDING,
    _SPUSDC_V2,
)


def test_assets_outstanding_selector_matches_keccak():
    keccak = pytest.importorskip("Crypto.Hash.keccak")
    k = keccak.new(digest_bits=256)
    k.update(b"assetsOutstanding()")
    assert _SEL_ASSETS_OUTSTANDING == "0x" + k.hexdigest()[:8]


def test_spusdc_v2_address_pinned():
    """The carve-out is vault-specific (spUSDC V2 is the only
    depositor-sourced slice at the Spark Eth ALM) — pin the address."""
    assert str(_SPUSDC_V2) == "0x28b3a8fb53b741a8fd78c0fb9a6b2393d896a43d"
