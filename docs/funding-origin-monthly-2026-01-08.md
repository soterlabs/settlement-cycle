# Funding-origin coverage: January-August 2026

Month-end acquisition capital, not monthly average balances or outstanding ilk debt.
Methods are allocation-specific; no Prime-wide reconciliation percentage is asserted.

## Traced borrowing as a percentage of allocation capital

| Prime / allocation | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| spark S1 | 99.06% | 99.23% | 99.58% | 95.27% | 98.22% | 98.40% | 99.54% | 99.85% |
| spark S32 | 82.23% | 81.72% | 89.20% | 87.70% | 89.90% | 88.51% | 89.26% | 92.18% |
| spark S34 | dust | dust | dust | dust | dust | dust | dust | 93.47% |
| spark S23 | 92.43% | 92.43% | 92.44% | 92.44% | 75.79% | 76.72% | 76.72% | 71.00% |
| grove E9 | 99.77% | 99.77% | 99.80% | 99.84% | 99.84% | 99.84% | 99.84% | 99.84% |
| grove E10 | 100.00% | 99.98% | 99.99% | 99.99% | 99.99% | 99.99% | 99.99% | 99.99% |
| grove E8/E20 | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| grove E42 | — | — | — | — | — | — | 99.99% | 99.99% |
| grove E23 | — | — | 100.00% | 99.73% | 99.71% | 99.83% | 99.81% | 99.81% |

## Capital whose origin remains unclassified (USD millions)

| Prime / allocation | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| spark S1 | 5.251 | 2.123 | 0.625 | 6.988 | 9.761 | 7.796 | 3.498 | 1.050 |
| spark S32 | 69.758 | 145.650 | 147.843 | 211.561 | 159.028 | 134.806 | 61.639 | 54.961 |
| spark S34 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 18.485 |
| spark S23 | 11.348 | 11.348 | 11.348 | 11.348 | 60.555 | 60.555 | 60.555 | 60.555 |
| grove E9 | 1.277 | 1.277 | 2.369 | 1.921 | 1.333 | 1.333 | 1.333 | 1.333 |
| grove E10 | 0.000 | 0.075 | 0.090 | 0.090 | 0.065 | 0.065 | 0.065 | 0.060 |
| grove E8/E20 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| grove E42 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.043 | 0.043 |
| grove E23 | 0.000 | 0.000 | 0.000 | 0.059 | 0.128 | 0.128 | 0.178 | 0.178 |

E23 separately identifies reinvested gains; these are excluded from its unresolved column.
Zero capital is shown as —; balances below $1 are shown as dust. Exact amounts remain in the JSON.
S34 holds only dust at January-July month ends; percentages on those amounts are not meaningful.
S32 now includes receipt research across all eight month ends; remaining unclassified capital still needs investigation.

## Configured venue inventory

“Not audited” means no reviewed allocation-specific origin result in this report, not zero borrowed funding.
A missing normalized account requires extraction/support assessment before attribution.

| Prime | Venue | Allocation | Status |
|---|---|---|---|
| spark | S1 | Spark USDS (SparkLend spToken) | audited |
| spark | S2 | Spark USDC (SparkLend spToken) | not audited |
| spark | S3 | Spark USDT (SparkLend spToken) | not audited |
| spark | S4 | Spark DAI (SparkLend spToken) | not audited |
| spark | S5 | Spark PYUSD (SparkLend spToken) | not audited |
| spark | S6 | Aave Ethereum Lido USDS (aToken) | not audited |
| spark | S7 | Aave Ethereum USDS (aToken) | not audited |
| spark | S8 | Aave Ethereum USDC (aToken) | not audited |
| spark | S9 | Aave Ethereum USDT (aToken) | not audited |
| spark | S10 | Spark Blue Chip USDC Vault (Morpho) | not audited |
| spark | S11 | Spark Blue Chip USDT Vault (Morpho — pre-2026-06 deployment) | not audited |
| spark | S65 | Spark Blue Chip USDT Vault (Morpho — 2026-06 redeployment) | not audited |
| spark | S12 | Spark DAI Vault (Morpho) | not audited |
| spark | S13 | Spark USDS Vault (Morpho) | not audited |
| spark | S14 | Maple syrupUSDC (ERC-4626) | not audited |
| spark | S15 | Maple syrupUSDT (ERC-4626) | not audited |
| spark | S16 | Ethena Staked USDe (sUSDe) | not audited |
| spark | S17 | Fluid Savings USDS (fsUSDS) | not audited |
| spark | S18 | Arkis Spark Prime USDC 1 (ERC-4626) | not audited |
| spark | S19 | BlackRock USD Institutional Digital Liquidity Fund (BUIDL-I) | not audited |
| spark | S20 | Janus Henderson Anemoy Treasury Fund (JTRSY) | not audited |
| spark | S21 | Superstate Short Duration US Government Securities Fund (USTB) | not audited |
| spark | S22 | Superstate Crypto Carry Fund (USCC) | not audited |
| spark | S23 | Anchorage tri-party loan escrow (USDC pass-through, ~$0 balance) | audited |
| spark | S24 | Spark.fi USDT Reserve Curve (sUSDS/USDT) | not audited |
| spark | S25 | Spark.fi PYUSD Reserve Curve (PYUSD/USDS) | not audited |
| spark | S61 | Spark.fi PYUSD Reserve Uniswap V4 (PYUSD/USDS) | not audited |
| spark | S62 | Spark.fi USDT Reserve Uniswap V4 (USDT/USDS) | not audited |
| spark | S66 | Spark.fi RLUSD Reserve Uniswap V4 (RLUSD/USDS) | not audited |
| spark | S26 | USDC raw (ALM idle) | not audited |
| spark | S27 | USDT raw (ALM idle — $442M as of 2026-04) | not audited |
| spark | S28 | PYUSD raw (ALM idle — $677M as of 2026-04) | not audited |
| spark | S64 | RLUSD raw (ALM idle — $251.7M as of 2026-08) | not audited |
| spark | S29 | DAI raw (ALM idle) | not audited |
| spark | S30 | USDe raw (ALM idle) | not audited |
| spark | S31 | USDS raw / POL (ALM idle — already netted out of utilized) | not audited |
| spark | S32 | sUSDS raw / POL (ALM — Cat B 4626, demand-side spread) | audited |
| spark | S34 | Spark USDC Vault (Morpho, Base) | audited |
| spark | S35 | Aave Base USDC (aBasUSDC) | not audited |
| spark | S36 | Fluid Savings USDS (fsUSDS, Base) | not audited |
| spark | S37 | Savings USDS / sUSDS proxy (Base — POL) | not audited |
| spark | S38 | USDS raw (Base — POL) | not audited |
| spark | S39 | USDC raw (Base — ALM idle) | not audited |
| spark | S41 | Aave Arbitrum USDCn (aArbUSDCn) | not audited |
| spark | S42 | Fluid Savings USDS (fsUSDS, Arbitrum) | not audited |
| spark | S43 | Savings USDS / sUSDS proxy (Arbitrum — POL) | not audited |
| spark | S44 | USDS raw (Arbitrum — POL) | not audited |
| spark | S45 | USDC raw (Arbitrum — ALM idle) | not audited |
| spark | S47 | Savings USDS / sUSDS proxy (Optimism — POL) | not audited |
| spark | S48 | USDS raw (Optimism — POL) | not audited |
| spark | S49 | USDC raw (Optimism — ALM idle) | not audited |
| spark | S51 | Savings USDS / sUSDS proxy (Unichain — POL) | not audited |
| spark | S52 | USDS raw (Unichain — POL) | not audited |
| spark | S53 | USDC raw (Unichain — ALM idle) | not audited |
| spark | S54 | Aave Avalanche USDC (aAvaUSDC) | not audited |
| spark | S55 | USDC raw (Avalanche-C — ALM idle) | not audited |
| spark | S56 | Spark Savings V2 — spUSDC vault | not in normalized snapshot |
| spark | S57 | Spark Savings V2 — spUSDT vault | not in normalized snapshot |
| spark | S58 | Spark Savings V2 — spETH vault | not in normalized snapshot |
| spark | S59 | Spark Savings V2 — spPYUSD vault | not in normalized snapshot |
| spark | S60 | Spark Savings V2 — spUSDC vault (Avalanche-C, CREATE2 same address) | not in normalized snapshot |
| spark | S63 | Spark Savings V2 — spUSDG vault (Robinhood Chain) | not in normalized snapshot |
| grove | E1 | Aave Horizon RWA RLUSD (aToken) | not audited |
| grove | E2 | Aave Horizon RWA USDC (aToken) | not audited |
| grove | E3 | Aave Ethereum RLUSD (aToken) | not audited |
| grove | E4 | Grove x Steakhouse USDC (Morpho 4626) | not audited |
| grove | E5 | Grove x Steakhouse USDC High Yield (Morpho 4626) | not audited |
| grove | E6 | Grove x Steakhouse AUSD (Morpho 4626) | not audited |
| grove | E37 | Maple syrupUSDC (ERC-4626) | not audited |
| grove | E7 | Securitize Tokenized AAA CLO Fund (STAC) | not audited |
| grove | E8 | Janus Henderson Anemoy AAA CLO (JAAA) | audited |
| grove | E9 | Janus Henderson Anemoy Treasury Fund (JTRSY) | audited |
| grove | E10 | BlackRock USD Institutional Digital Liquidity Fund (BUIDL-I) | audited |
| grove | E11 | Curve AUSD/USDC stableswap LP | not audited |
| grove | E12 | Uniswap V3 AUSD/USDC pool (NFT positions) | not audited |
| grove | E30 | Uniswap V3 AUSD/USDC pool (NFT positions — alt holder) | not in normalized snapshot |
| grove | E33 | Uniswap V3 AUSD/USDC pool (Monad, NFT positions) | not in normalized snapshot |
| grove | E13 | RLUSD raw (ALM idle) | not audited |
| grove | E14 | AUSD raw (ALM idle) | not audited |
| grove | E31 | AUSD raw (alt holder idle) | not in normalized snapshot |
| grove | E15 | USDC raw (ALM idle) | not audited |
| grove | E32 | USDC raw (alt holder idle) | not in normalized snapshot |
| grove | E16 | DAI raw (ALM idle) | not audited |
| grove | E17 | USDS raw / POL (ALM idle — already netted out of utilized) | not audited |
| grove | E18 | sUSDS raw / POL (ALM idle — Cat B 4626) | not audited |
| grove | E36 | OOB principal via 0xd94f (relay to Monad ALM EOA → AUSD acquisition) | not audited |
| grove | E19 | Grove x Steakhouse USDC High Yield (Base, Morpho 4626) | not audited |
| grove | E23 | Steakhouse Prime Instant (Base, Morpho V2) | audited |
| grove | E20 | Janus Henderson Anemoy AAA CLO (JAAA-avalanche) | audited |
| grove | E21 | Galaxy Arch CLO Token (GACLO-1) | not audited |
| grove | E22 | Anemoy Tokenized Apollo Diversified Credit Fund (ACRDX) | not audited |
| grove | E24 | Steakhouse High Yield PYUSD (Morpho 4626) | not audited |
| grove | E26 | PYUSD raw (ALM idle) | not audited |
| grove | E27 | USDC raw on Base (ALM idle) | not audited |
| grove | E25 | Grove x Steakhouse High Yield AUSD (Monad, Morpho 4626) | not in normalized snapshot |
| grove | E34 | AUSD raw (Monad, alt holder idle) | not in normalized snapshot |
| grove | E35 | USDC raw (Monad, alt holder idle) | not in normalized snapshot |
| grove | E38 | Agora AUSD incentives (cash distribution to Grove Eth ALM) | not in normalized snapshot |
| grove | E40 | USDS raw (Diamond PAU ALM idle) | not audited |
| grove | E41 | JTRSY Basin escrow — USDS pending subscription (Diamond PAU) | not in normalized snapshot |
| grove | E42 | Galaxy Warehouse (off-chain facility — cash distribution to Grove Eth ALM) | audited |
