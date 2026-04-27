# ALM asset catalog

Every asset that has ever touched an ALM Proxy, classified by the function needed to value the position.

- **Source:** Dune query [7359593](https://dune.com/queries/7359593) — distinct (blockchain, contract_address, symbol) for every token transferred in/out of the 12 ALMs in scope
- **Companion docs:** [ALM_COUNTERPARTIES.md](ALM_COUNTERPARTIES.md) for counterparty routing, [VALUATION_METHODOLOGY.md](VALUATION_METHODOLOGY.md) for the Dune SQL patterns, [valuation_poc/COMPARISON.md](valuation_poc/COMPARISON.md) for the Dune↔Python convergence run
- **Snapshot:** 2026-04-23
- **Real-asset count:** 56 tokens after filtering spam
- **Dropped:** ~80 unlabeled "ERC20" and unicode-lookalike "USDC" contracts — these are airdrop/phishing tokens, not holdings

## Pricing-function taxonomy

Position value = **balance × price**. The choice of how to compute each side splits the asset universe into eight categories:

| # | Category | Balance getter | Price getter | POC-verified? |
|---|---|---|---|---|
| A | Par stablecoin ERC-20 | `balanceOf(alm) / 10^decimals` | `= 1.0 USD` | ✅ Dune + Python match exactly |
| B | ERC-4626 vault share | `balanceOf(alm)` | `convertToAssets(1e18) / 1e18 × underlying_price` | ✅ Python (sUSDS: $1.49B) |
| C | Aave v3 aToken (rebasing) | `balanceOf(alm)` — already rebased | `= underlying_price` | ✅ Dune M2 (scaled×index) matches Python within 0.001% |
| D | SparkLend spToken (Aave-v3 fork) | `balanceOf(alm)` — rebasing, same as aToken | `= underlying_price` | ✅ Dune M2 matches Python within 0.001% |
| E | RWA tranche / issuer token | `balanceOf(alm)` | Off-chain NAV (issuer API or CoinGecko); **no on-chain getter** (JTRSY probe reverted) — $1 placeholder understates Grove's JTRSY by ~10% | ✅ Python (JTRSY: $1.455B @ CoinGecko vs $1.323B placeholder) |
| F | LP / pool share token | `balanceOf(alm)` | **Method B (preferred):** `share × Σ balance[i] × price[i]` via `pool.balances()` + underlying prices. **Method A (shortcut):** `get_virtual_price() / 1e18` — simpler but lags when a coin accrues yield externally | ✅ Python (sUSDSUSDT LP: $50.0M, Method A vs B differ by 0.004%) |
| G | Native / wrapped gas | `eth_getBalance(alm)` (native) or `balanceOf(alm)` (wrapped) | Chainlink/CoinGecko | ✅ Python only — native ETH not in `tokens.transfers`; recommend excluding from PnL |
| H | Governance reward | `balanceOf(alm)` | `prices.minute` (Dune) or DEX/oracle | ✅ Dune and Python match on balance; ~$0.01 price-source gap |

### Critical caveat on Dune `tokens.transfers`

For **rebasing aTokens / spTokens (categories C and D)**, `tokens.transfers` mixes semantics:
- `Transfer` from **Mint/Burn** emits **NOMINAL** amounts (underlying + accrued interest at the event moment)
- `Transfer` from **user-to-user transfers** emits **SCALED** amounts (nominal × RAY / liquidityIndex)

Summing them gives the **nominal balance as of the last event**. POC measured this drift at 0.0008% on $208M aHorRwaRLUSD and 0.0015% on $13.8M spUSDC — small, but the correct Dune primitive is `atoken_evt_{mint,burn,balancetransfer}` × current `liquidityIndex`, detailed in [VALUATION_METHODOLOGY.md §0.4](VALUATION_METHODOLOGY.md#04-scaled-balance-getter-rebasing-atokens--sptokens).

Correct balance sources for C/D:
- **Best:** eth_call `aToken.balanceOf(alm)` at block boundary
- **Next:** `scaled_balance(alm) × liquidityIndex(reserve) / 1e27` from `aave_v3_ethereum.Pool_evt_ReserveDataUpdated`
- **Interim:** accept the drift (few bps/month), realized PnL corrects at next withdrawal

---

## Category A — Par stablecoin ERC-20

Priced at exactly `$1.00`. Balance is `tokens.erc20_transfers` cumulative net.

| Symbol | Chain | Address | Decimals | Notes |
|---|---|---|---:|---|
| USDC | ethereum | `0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48` | 6 | Circle |
| USDC | base | `0x833589fcd6edb6e08f4c7c32d4f71b54bda02913` | 6 | Circle native |
| USDC | arbitrum | `0xaf88d065e77c8cc2239327c5edb3a432268e5831` | 6 | Circle native |
| USDC | optimism | `0x0b2c639c533813f4aa9d7837caf62653d097ff85` | 6 | Circle native |
| USDC | unichain | `0x078d782b760474a361dda0af3839290b0ef57ad6` | 6 | Circle native |
| USDC | avalanche_c | `0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e` | 6 | Circle native |
| USDC | plume | `0x222365ef19f7947e5484218551b56bb3965aa7af` | 6 | Circle (pUSD? confirm) |
| USDC | monad | `0x754704bc059f8c67012fed69bc8a327a5aafb603` | 6 | Monad testnet/mainnet Circle |
| USDT | ethereum | `0xdac17f958d2ee523a2206206994597c13d831ec7` | 6 | Tether |
| DAI | ethereum | `0x6b175474e89094c44da98b954eedeac495271d0f` | 18 | MakerDAO legacy |
| USDS | ethereum | `0xdc035d45d973e3ec169d2276ddab16f1e407384f` | 18 | Sky USDS |
| USDS | base | `0x820c137fa70c8691f0e44dc420a5e53c168921dc` | 18 | Sky USDS canonical-bridged |
| PYUSD | ethereum | `0x6c3ea9036406852006290770bedfcaba0e23a0e8` | 6 | Paxos / PayPal |
| RLUSD | ethereum | `0x8292bb45bf1ee4d140127049757c2e0ff06317ed` | 18 | Ripple USD |
| AUSD | ethereum | `0x00000000efe302beaa2b3e6e1b18d08d69a9012a` | 6 | Agora AUSD |
| AUSD | monad | `0x00000000efe302beaa2b3e6e1b18d08d69a9012a` | 6 | Agora AUSD (same CREATE2 address) |
| USDe | ethereum | `0x4c9edd5852cd905f086c759e8383e09bff1e68b3` | 18 | Ethena USDe (peg-stable, treat as Category A) |

**Value function:**

```python
value_usd = balance_raw / (10 ** decimals) * 1.00
```

---

## Category B — ERC-4626 vault share

Value = shares × conversion_rate × underlying_price. The conversion rate is monotonically non-decreasing (yield accrues into it).

| Symbol | Chain | Address | Underlying | Protocol |
|---|---|---|---|---|
| sUSDS | ethereum | `0xa3931d71877c0e7a3148cb7eb4463524fec27fbd` | USDS | Sky staking (SSR) |
| sUSDS | base | `0x5875eee11cf8398102fdad704c9e96607675467a` | USDS | Sky (canonical-bridged) |
| sUSDS | arbitrum | `0xddb46999f8891663a8f2828d25298f70416d7610` | USDS | Sky |
| sUSDS | optimism | `0xb5b2dc7fd34c249f4be7fb1fcea07950784229e0` | USDS | Sky |
| sUSDe | ethereum | `0x9d39a5de30e57443bff2a8307a4256c8797a3497` | USDe | Ethena staking |
| fsUSDS | ethereum | `0x2bbe31d63e6813e3ac858c04dae43fb2a72b0d11` | USDS | Spark farming vault |
| fsUSDS | base | `0xf62e339f21d8018940f188f6987bcdf02a849619` | USDS | Spark farming (Base) |
| syrupUSDC | ethereum | `0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b` | USDC | Maple v2 |
| syrupUSDT | ethereum | `0x356b8d89c1e1239cbbb9de4815c39a1474d5ba7d` | USDT | Maple v2 |
| sparkUSDC | base | `0x7bfa7c4f149e7415b73bdedfe609237e29cbf34a` | USDC | MetaMorpho (Spark-curated) |
| sparkPrimeUSDC1 | ethereum | `0x38464507e02c983f20428a6e8566693fe9e422a9` | USDC | MetaMorpho SPK Prime |
| steakUSDC | base | `0xbeef0e0834849acc03f0089f01f4f1eeb06873c9` | USDC | MetaMorpho (Steakhouse) |
| grove-bbqUSDC | ethereum | `0xbeeff08df54897e7544ab01d0e86f013da354111` | USDC | MetaMorpho (BBQ Labs) |
| grove-bbqUSDC (2) | ethereum | `0xbeef2b5fd3d94469b7782aebe6364e6e6fb1b709` | USDC | MetaMorpho (BBQ Labs) — second vault |
| grove-bbqAUSD | ethereum | `0xbeeff0d672ab7f5018dfb614c93981045d4aa98a` | AUSD | MetaMorpho (BBQ Labs) |
| grove-bbqAUSD | monad | `0x32841a8511d5c2c5b253f45668780b99139e476d` | AUSD | MetaMorpho (BBQ Labs) — Monad |

**Value function:**

```python
# Option 1 — canonical ERC-4626 call (most accurate, works on-chain now)
shares = balanceOf(alm)
assets = convertToAssets(shares)     # ERC-4626 method
value_usd = assets / (10 ** underlying_decimals) * underlying_price

# Option 2 — no eth_call available, derive price-per-share from total supply
pps = totalAssets() / totalSupply()
value_usd = shares / (10 ** share_decimals) * pps * underlying_price
```

---

## Category C — Aave v3 aToken (rebasing)

`balanceOf()` returns the live index-scaled amount. Transfer events emit **nominal** deltas, so transfer-sum under-reports. Always prefer balanceOf over cumulative transfer delta.

| Symbol | Chain | Address | Underlying | Market |
|---|---|---|---|---|
| aEthUSDC | ethereum | `0x98c23e9d8f34fefb1b7bd6a91b7ff122f4e16f5c` | USDC | Aave v3 core |
| aEthUSDT | ethereum | `0x23878914efe38d27c4d67ab83ed1b93a74d4086a` | USDT | Aave v3 core |
| aEthUSDS | ethereum | `0x32a6268f9ba3642dda7892add74f1d34469a4259` | USDS | Aave v3 core |
| aEthLidoUSDS | ethereum | `0x09aa30b182488f769a9824f15e6ce58591da4781` | USDS | Aave v3 Lido market |
| aEthRLUSD | ethereum | `0xfa82580c16a31d0c1bc632a36f82e83efef3eec0` | RLUSD | Aave v3 core |
| aHorRwaRLUSD | ethereum | `0xe3190143eb552456f88464662f0c0c4ac67a77eb` | RLUSD | Aave v3 Horizon (RWA) |
| aHorRwaUSDC | ethereum | `0x68215b6533c47ff9f7125ac95adf00fe4a62f79e` | USDC | Aave v3 Horizon (RWA) |
| aBasUSDC | base | `0x4e65fe4dba92790696d040ac24aa414708f5c0ab` | USDC | Aave v3 Base |
| aArbUSDCn | arbitrum | `0x724dc807b04555b71ed48a6896b6f41593b8c637` | USDC | Aave v3 Arbitrum (native USDC) |
| aAvaUSDC | avalanche_c | `0x625e7708f30ca75bfd92586e17077590c60eb4cd` | USDC | Aave v3 Avalanche |

**Value function:**

```python
# Primary: eth_call balanceOf — returns post-rebase, ready to price
balance_raw = balanceOf(alm)   # ERC-20 method — includes accrued interest
value_usd = balance_raw / (10 ** underlying_decimals) * underlying_price

# Fallback if no eth_call (Dune-only):
# scaled = Σ nominal transfers (from tokens.transfers)
# index_now = Pool.getReserveData(underlying).liquidityIndex  # from Pool_evt_ReserveDataUpdated
# balance_raw = scaled × index_now / 1e27
```

---

## Category D — SparkLend spToken (Aave v3 fork, same mechanics as C)

SparkLend deployed two generations on Ethereum — V1 (legacy markets, some deprecated) and the newer Lite/Prime markets. Both use Aave v3 aToken mechanics.

| Symbol | Chain | Address | Underlying | Generation |
|---|---|---|---|---|
| spDAI (v1) | ethereum | `0x4dedf26112b3ec8ec46e7e31ea5e123490b05b8b` | DAI | SparkLend V1 (legacy) |
| spDAI (v2/Lite) | ethereum | `0x73e65dbd630f90604062f6e02fab9138e713edd9` | DAI | SparkLend V2 / Prime |
| spUSDC | ethereum | `0x377c3bd93f2a2984e1e7be6a5c22c525ed4a4815` | USDC | SparkLend |
| spUSDT | ethereum | `0xe7df13b8e3d6740fe17cbe928c7334243d86c92f` | USDT | SparkLend |
| spUSDS | ethereum | `0xc02ab1a5eaa8d1b114ef786d9bde108cd4364359` | USDS | SparkLend |
| spPYUSD | ethereum | `0x779224df1c756b4edd899854f32a53e8c2b2ce5d` | PYUSD | SparkLend |
| spWETH | ethereum | `0x59cd1c87501baa753d0b5b5ab5d8416a45cd71db` | WETH | SparkLend |
| sparkUSDCbc | ethereum | `0x56a76b428244a50513ec81e225a293d128fd581d` | USDC | Bridged-collateral variant |
| sparkUSDTbc | ethereum | `0xc7cdcfdefc64631ed6799c95e3b110cd42f2bd22` | USDT | Bridged-collateral variant |
| sparkUSDS | ethereum | `0xe41a0583334f0dc4e023acd0bfef3667f6fe0597` | USDS | SparkLend USDS market (2025) |

**Value function:** identical to Category C — use `balanceOf(alm)` and multiply by underlying price.

**Note on the two `spDAI` contracts:** the transfer data shows both addresses emitting spDAI activity. `0x4dedf26` has flow concentrated 2025-04 → present; `0x73e65dbd` has flow concentrated 2025-06 → 2025-09. Likely one is deprecated. Need to confirm which is the live market before relying on balances.

---

## Category E — RWA tranche / issuer token (NAV from off-chain feed)

None of these expose a standard on-chain NAV getter. Current treatment: **$1.00 placeholder**, which understates MtM by the accrued coupon drift (~0.5%/month at 4–6% APY) — see [grove/QUESTIONS.md](grove/QUESTIONS.md) §Q2.

| Symbol | Chain | Address | Issuer | NAV feed source |
|---|---|---|---|---|
| BUIDL-I | ethereum | `0x6a9da2d710bb9b700acde7cb81f10f1ff8c89041` | BlackRock | Issuer-published; may rebase balances |
| JAAA | ethereum | `0x5a0f93d040de44e78f251b03c43be9cf317dcf64` | Janus / Anemoy (AAA CLO) | Centrifuge pool manager (off-chain) |
| JAAA | avalanche_c | `0x58f93d6b1ef2f44ec379cb975657c132cbed3b6b` | Janus / Anemoy | Centrifuge |
| JTRSY | ethereum | `0x8c213ee79581ff4984583c6a801e5263418c4b86` | Janus / Anemoy (Treasury) | Centrifuge |
| JTRSY | plume | `0xa5d465251fbcc907f5dd6bb2145488dfc6a2627b` | Janus / Anemoy | Centrifuge |
| USTB | ethereum | `0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e` | Superstate | Superstate API |
| USCC | ethereum | `0x14d60e7fdc0d71d8611742720e4c50e7a974020c` | Superstate (Crypto Carry) | Superstate API |

Not observed in transfer flow but documented as allocation targets (see [grove/QUESTIONS.md](grove/QUESTIONS.md) §Q2): **STAC** (Securitize E7 CLO). Add to catalog if/when it shows up.

**Value function (interim):**

```python
value_usd = balance_raw / (10 ** decimals) * 1.00   # $1 placeholder

# Target (when NAV feed wired):
# value_usd = balance_raw / (10 ** decimals) * nav_per_token_usd
```

---

## Category F — LP / pool share token

Valued by reconstructing the underlying reserves per LP unit.

| Symbol | Chain | Address | Pool |
|---|---|---|---|
| sUSDSUSDT | ethereum | `0x00836fe54625be242bcfa286207795405ca4fd10` | Curve sUSDS/USDT stableswap |
| PYUSDUSDS | ethereum | `0xa632d59b9b804a956bfaa9b48af3a1b74808fc1f` | Curve PYUSD/USDS stableswap |

**Curve stableswap value function:**

```python
# Simple (trusts the pool's virtual price):
vp = pool.get_virtual_price()   # scaled 1e18
lp_balance = balanceOf(alm)
value_usd = lp_balance * vp / 1e18 / (10 ** lp_decimals) * $1.00  # stableswap ≈ 1 USD per vp unit

# Strict (compute from reserves):
share = lp_balance / totalSupply()
for i, coin in enumerate(pool.coins):
    underlying_i = pool.balances(i) * share
    value_usd += underlying_i / (10 ** coin.decimals) * coin_price
```

Not yet observed in the ALM transfer log, but documented Grove venues:
- **E11 Curve AUSD/USDC LP** — deferred per [grove/QUESTIONS.md](grove/QUESTIONS.md) §Q10
- **E12 Uniswap V3 AUSD/USDC NFT** — deferred per same §Q10 (requires tick-math valuation)

---

## Category G — Native / wrapped gas

Negligible PnL contribution but needed to track gas spend.

| Symbol | Chain | Address | Price feed |
|---|---|---|---|
| ETH | ethereum | `0x0000…0000` (trace) | Chainlink ETH/USD |
| WETH | ethereum | `0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2` | Chainlink ETH/USD |
| AVAX | avalanche_c | `0x0000…0000` (trace) | Chainlink AVAX/USD |
| MON | monad | `0x0000…0000` (trace) | not yet in Dune price spell |
| WMON | monad | `0x3bd359c1119da7da1d913d1c4d2b7c461115433a` | not yet in Dune price spell |

**Value function:**

```python
value_usd = balance_raw / (10 ** decimals) * chainlink_price(symbol)
```

---

## Category H — Governance / reward token

Earned passively (Morpho distributes `MORPHO` rewards). Hold-to-redeem value, minor vs. core book.

| Symbol | Chain | Address |
|---|---|---|
| MORPHO | base | `0xbaa5cc21fd487b8fcc2f632f3f4e8d37262a0842` |
| MORPHO | ethereum | (observed in counterparty flows but not in distinct-tokens top-100) |

**Value function:** DEX oracle market price × balance.

---

## Excluded — spam / phishing

Dropped from the catalog entirely:

- **~75 unlabeled "ERC20" contracts** on Ethereum. These fire transfers from Spark's ALM address, but Spark never approved or accepted them — they are pull-type or dust-spam airdrops probing for interactions. None carry USD pricing.
- **9 unicode-lookalike "USDC" contracts** on arbitrum and optimism (symbol field contains invisible zero-width chars or Cyrillic С). Classic phishing spam impersonating Circle USDC.

Full list in the source Dune query. These show up in `tokens.transfers` but should not feed any position valuation.

---

## Open gaps

1. **Plume token coverage** — only JTRSY and USDC show up on Plume (6 transfers each). Real Grove Plume book likely includes Pell, Apollo, other venues; `tokens.transfers` does not fully index Plume yet. See [grove/QUESTIONS.md](grove/QUESTIONS.md) §Q4.
2. **Monad token coverage** — only AUSD, grove-bbqAUSD, USDC, MON/WMON. The Phase-1 monad allocation plan likely includes more venues.
3. **STAC (Securitize CLO)** not seen in transfers — confirm whether E7 venue is actually active.
4. **sparkUSDS (0xe41a0583)** — SparkLend USDS market; verify it uses Aave v3 aToken mechanics (Category D) vs a different interface.
5. **sparkPrimeUSDC1** address `0x38464507` — transfer count is only 6. Might be a staging/test vault. Confirm production deployment.
6. **Duplicate spDAI addresses** — resolve which is current production before using for live valuation.
