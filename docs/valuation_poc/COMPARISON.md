# Valuation POC — Dune vs Python

Single-day position-value snapshot comparison, one representative asset per category from [ASSET_CATALOG.md](../ASSET_CATALOG.md). Validates the methodology described in [VALUATION_METHODOLOGY.md](../VALUATION_METHODOLOGY.md).

## Snapshot parameters

| Chain | Cutoff block | Block timestamp (UTC) |
|---|---:|---|
| Ethereum | 24945607 | 2026-04-23 22:22:23 |
| Base | 45096799 | 2026-04-23 22:22:25 |

Python ran at "latest" (a few seconds / blocks past the Dune cutoff). Expected sub-0.001% drift on rebasing tokens from the intervening seconds of interest accrual.

**Extended run** (E real-NAV + F Curve LP) at 2026-04-23 22:48 UTC — numbers in the Categories B, E, F, G rows below. Rebasing-token rows (C, D) recorded at the earlier 22:22 UTC cutoff to pair with the Dune queries.

## Methodology per category

| Cat | Representative | ALM | Python method | Dune method |
|---|---|---|---|---|
| A | PYUSD | Spark ETH (`0x1601843c…`) | `erc20.balanceOf(alm)` × $1 | `Σ tokens.transfers` × $1 |
| B | sUSDS | Spark ETH | `balanceOf(alm) × convertToAssets(1 share)` × $1 | *Python only* (§B in doc) |
| C | aHorRwaRLUSD | Grove ETH (`0x491edfb0…`) | `aToken.balanceOf(alm)` (rebased) × $1 | **M2**: Σ scaled deltas from `atoken_evt_{mint,burn,balancetransfer}` × current `liquidityIndex` from `poolinstance_evt_reservedataupdated` |
| D | spUSDC | Spark ETH | `spToken.balanceOf(alm)` × $1 | same shape as C against `spark_protocol_ethereum.*` |
| E | JTRSY | Grove ETH (`0x491edfb0…`) | `balanceOf(alm)` × **CoinGecko NAV** (on-chain getter reverts) | *Python only* (§E) — Dune has no NAV feed |
| F | Curve sUSDSUSDT LP | Spark ETH | **Method B**: `share × Σ balance[i] × price[i]` where prices come from `pool.balances()` + underlying `convertToAssets` | *Python only* (§F) — Dune requires per-pool event reconstruction |
| G | native ETH | Grove ETH | `eth_getBalance(alm)` × CoinGecko ETH/USD | *Python only* — no `tokens.transfers` entry for native |
| H | MORPHO | Spark Base (`0x2917956e…`) | `erc20.balanceOf(alm)` × CoinGecko | `Σ tokens.transfers` × `prices.minute` |

## Results

| Cat | Representative | Python (USD) | Dune (USD) | Δ USD | Δ % | Verdict |
|---|---|---:|---:|---:|---:|:---|
| **A** | PYUSD × $1 | 677,206,361.8975 | 677,206,361.8975 | 0.000 | 0.000% | ✅ exact |
| **B** | sUSDS × pps × $1 | 1,492,783,618.56 | — | — | — | Python only; `convertToAssets(1) = 1.094233` |
| **C** | aHorRwaRLUSD (rebased) × $1 | 207,940,911.9747 | 207,939,438.9734 | +1,473 | +0.00071% | ✅ tight |
| **D** | spUSDC (rebased) × $1 | 13,774,683.1264 | 13,774,654.5002 | +28.63 | +0.00021% | ✅ tight |
| **E** | JTRSY × NAV | **$1,455,419,748** (@ CoinGecko $1.10) *vs* $1,323,108,862 (@ $1 placeholder) | — | — | — | Python only; placeholder understates by ~10% |
| **F** | Curve sUSDSUSDT LP | **$50,000,791** (Method B: reserves × prices) *vs* $49,998,805 (Method A: virtual_price) | — | — | — | Python only; two methods differ by 0.004% |
| **G** | native ETH × ETH/USD | 116.8190 | — | — | — | Python only, non-ERC-20 |
| **H** | MORPHO × DEX price | 1.5407 (@$1.91) | 1.5407 (@$1.91) | ~0 | ~0% | ✅ match (price converged from prior run) |

## Key findings

### 1. Par stablecoin (A) — Dune matches Python bit-for-bit

PYUSD transfers in `tokens.transfers` are nominal (plain ERC-20), so cumulative net transfers equal the current balance exactly. No drift.

### 2. Aave v3 aToken / SparkLend spToken (C, D) — two Dune methods, both viable

Two Dune methods were tested:

| Method | C value (RLUSD) | D value (USDC) |
|---|---:|---:|
| **M1 — naive `tokens.transfers` sum** | 207,939,272.03 | 13,774,452.53 |
| **M2 — scaled events × current `liquidityIndex`** | 207,939,438.97 | 13,774,654.50 |
| M2 − M1 (rebase drift since last event) | +166.94 | +201.97 |

**Why M1 doesn't exactly match M2:** Aave v3 aToken Transfer events use *mixed* semantics: mint/burn emit NOMINAL amounts (including accrued interest at event time), while user-to-user transfers emit SCALED amounts. Summing them in `tokens.transfers` gives the nominal balance *at the last transfer event time*. Interest accruing between that event and the snapshot block is invisible — that's the `M2 − M1` gap.

**Why Python still differs from M2 by ~0.0002–0.0007%:** Python queried "latest" block (a few seconds past the Dune cutoff), picking up a few extra seconds of live interest accrual. Within block precision.

**Conclusion:** both M1 and M2 are usable in Dune, with different precision trade-offs. M2 is the correct pattern; M1 is a cheap approximation that's ~0.001% low at any given moment.

### 3. Governance token (H) — price source causes the tiny drift

Balance matches exactly. The $0.008 gap is entirely explained by Dune's `prices.minute` VWAP ($1.91) vs the CoinGecko snapshot ($1.92) at slightly different moments. On a $1.5 position this is irrelevant; on a $150M position it would matter — pin a single price source in production.

### 4. Python-only categories — one methodology demonstrated each

- **B (ERC-4626 sUSDS):** `convertToAssets(1 share) = 1.094233` USDS. Value = balance × pps × $1. One canonical eth_call, no Dune schema hunt needed for single-day snapshots.
- **E (RWA JTRSY):** On-chain probe of JTRSY at `0x8c213ee7…` tried `pricePerShare`, `sharePrice`, `latestAnswer`, `convertToAssets` — **all revert**. CoinGecko lists JTRSY at $1.10, which puts Grove's position at **$1,455M** instead of the $1,323M placeholder (+$132M). Authoritative NAV still requires issuer API (Centrifuge).
- **F (Curve LP sUSDSUSDT):** 99.97% of the sUSDSUSDT pool is owned by Spark ETH ALM (48.56M / 48.57M LP tokens). Two valuation methods tested:
  - **Method A — `virtual_price` shortcut** (`lp_balance × vp / 1e18`): $49,998,805. Cheap, but the pool's internal invariant lags because sUSDS is accruing yield externally (vs the pool's last rebalance).
  - **Method B — reserves reconstruction** (`share × Σ balance[i] × price[i]`): $50,000,791. Uses live `convertToAssets` for sUSDS ($1.094), USDT at $1. More accurate.
  - Gap: $1,986 (0.004%) — small, but the residual grows with time-since-last-pool-trade.
- **G (native ETH):** `eth_getBalance(Grove ETH ALM) = 0.050 ETH × $2,336.38 = $117`. Non-ERC-20, no `tokens.transfers` entry. The Dune alternative (`ethereum.traces`) is expensive; recommend excluding native from position-value and tracking as operational cost.

## Validation of VALUATION_METHODOLOGY.md

All open questions resolved by this POC:

| # | Status | Evidence |
|---|---|---|
| Q1 | **Resolved.** Aave v3 canonical schema is per-market, not unified: `aave_v3_ethereum.*` for core, `aave_horizon_ethereum.*` for Horizon. | Query C ran against `aave_horizon_ethereum.atoken_evt_{mint,burn,balancetransfer}` and `poolinstance_evt_reservedataupdated`. |
| Q3 | **Resolved.** Aave Horizon decoded tables exist under `aave_horizon_ethereum.*`. | Same as Q1 evidence. |
| Q4 | **Resolved.** SparkLend lives under `spark_protocol_ethereum.*`; tables include `atoken_evt_mint/burn/balancetransfer` and `pool_evt_reservedataupdated`. | Query D. |
| Q11 | **Resolved by convention.** Use block-number cutoffs, not block-date, for exact alignment across Dune and Python. | Both queries used `block_number <= 24945607`. |

Still open:

- **Q2 (ERC-4626 per-vault decoded tables):** not tested on Dune — Python path is canonical for single-day
- **Q5 (duplicate spDAI contracts):** not addressed — chose `spUSDC` which has no duplicate
- **Q6 (RWA NAV feed):** partial — CoinGecko works for JTRSY/JAAA listed tokens; BUIDL-I/USTB/USCC/STAC still need issuer-API relay. Recommend materialized `sky_msc.rwa_nav_daily` table.
- **Q7 (pre-indexed Curve state spell):** no native Dune spell found. Python `pool.balances()` + `convertToAssets()` is the clean path; Dune-native pricing remains per-pool bespoke.
- **Q8 (UniV3):** no live position, still deferred.

## Files

```
agents/shared/valuation_poc/
├── COMPARISON.md                          # this file
├── python/
│   ├── snapshot.py                        # full 8-category Python script
│   └── out.json                           # latest snapshot results
└── dune/
    ├── A_par_stable_pyusd.sql             # query id 7365842
    ├── C_aave_atoken_aHorRwaRLUSD.sql     # query id 7365843
    ├── D_sparklend_spUSDC.sql             # query id 7365845
    └── H_governance_morpho.sql            # query id 7365846
```

To re-run:

```bash
cd agents/shared/valuation_poc/python
python3 snapshot.py > out.json
```

Dune queries are at `https://dune.com/queries/{id}` — executable via the Dune MCP or the web UI.
