"""Pricing categories from `ASSET_CATALOG.md`.

Each category determines which Source implementation prices the venue token.
"""

from enum import StrEnum


class PricingCategory(StrEnum):
    """Eight pricing categories. See ASSET_CATALOG.md for examples per category."""

    PAR_STABLE = "A"           # USDC, USDT, DAI, USDS, PYUSD, RLUSD, AUSD, USDe → $1
    ERC4626_VAULT = "B"        # sUSDS, syrupUSDC, MetaMorpho — convertToAssets × underlying
    AAVE_ATOKEN = "C"          # aEth*, aHorRwa* — balanceOf is rebased; price = underlying
    SPARKLEND_SPTOKEN = "D"    # sp*, sparkUSDC*bc — same as Aave aToken
    RWA_TRANCHE = "E"          # BUIDL-I, JTRSY, JAAA, USTB — off-chain NAV
    LP_POOL = "F"              # Curve, Uni V3 — share × Σ reserves × prices
    NATIVE_GAS = "G"           # ETH, AVAX, MON, WETH — oracle / market
    GOVERNANCE = "H"           # MORPHO, … — DEX/oracle market price
