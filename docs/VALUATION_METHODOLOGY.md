# Position-value methodology (Dune)

How to compute **USD value of each ALM-held position using Dune SQL only** — no eth_call, no off-chain scraping. One section per asset category from [ASSET_CATALOG.md](ASSET_CATALOG.md).

- **Companion docs:** [ASSET_CATALOG.md](ASSET_CATALOG.md) (what to value), [ALM_COUNTERPARTIES.md](ALM_COUNTERPARTIES.md) (where it flows), [valuation_poc/COMPARISON.md](valuation_poc/COMPARISON.md) (Dune↔Python validation run)
- **Dune dialect:** DuneSQL (Trino)
- **Snapshot convention:** align on a single `block_number` (not `block_date`) for exact Dune↔Python agreement. Use `block_time` or `block_number` bounds, not date bounds, when cross-checking against a live RPC. This convention was validated in the POC (see §12).

---

## 0. Shared building blocks

Every category composes these three primitives:

### 0.1 Balance getter

Dune does not expose `balanceOf()` as a queryable. Balances must be derived from the transfer event stream.

**Default pattern** (all ERC-20 that are not rebasing):

```sql
-- Running balance of `token` held by `alm` up to `snapshot_date`
WITH net_flows AS (
  SELECT
    block_date,
    SUM(CASE WHEN "to"   = :alm THEN CAST(amount_raw AS int256) ELSE 0 END) -
    SUM(CASE WHEN "from" = :alm THEN CAST(amount_raw AS int256) ELSE 0 END) AS delta_raw
  FROM tokens.transfers
  WHERE blockchain        = :chain
    AND contract_address  = :token
    AND block_date       <= DATE :snapshot_date
    AND (    "to"   = :alm OR "from" = :alm )
  GROUP BY block_date
)
SELECT SUM(delta_raw) AS balance_raw FROM net_flows;
```

This works for Categories **A, B, E, F, G, H**. It does **not** work for **C and D** (see §0.4).

### 0.2 Price getter

Dune has two price spells:

| Spell | Coverage | Granularity | Use when |
|---|---|---|---|
| `prices.minute` | Liquid tokens indexed by Dune's price team (USDC, USDT, DAI, USDS, PYUSD, WETH, MORPHO, USDe, RLUSD after listing, etc.) | 1-minute bars, VWAP from DEX trades | Category G, H, and verification of stables |
| `prices_external.minute` | Same tokens, external CEX feed | 1-minute bars | cross-check |
| `prices_dex.minute` | Same tokens, DEX only | 1-minute bars | cross-check |

Schema: `prices.minute` and `prices.day` both expose columns `timestamp, blockchain, contract_address, symbol, price, decimals, volume, source, contract_address_varchar`. **Note:** the time column is `timestamp`, not `minute` — confirmed by the POC (initial query failed with `Column 'minute' cannot be resolved`).

Join pattern for EOD price — **always narrow by `timestamp` on both ends**, otherwise `prices.minute` hits `Query exceeds cluster capacity`:

```sql
SELECT price
FROM prices.minute
WHERE blockchain = :chain
  AND contract_address = :token
  AND timestamp >= TIMESTAMP :snapshot_ts - INTERVAL '12' HOUR
  AND timestamp <= TIMESTAMP :snapshot_ts
ORDER BY timestamp DESC
LIMIT 1
```

**Coverage gap.** `prices.minute` has no entry for RWA tokens (BUIDL-I, JAAA, JTRSY, USTB, USCC), vault shares (sUSDS, syrupUSDC, fsUSDS, sparkUSDC-vault, MetaMorpho curated), aTokens, spTokens, or Curve LP shares. Those need the per-category logic below.

**Price-source convergence check** (from POC §H): Dune `prices.minute` MORPHO/USD returned $1.91 while CoinGecko returned $1.92 at the same wall clock — $0.01 gap on small positions is noise, on $150M positions it's $790K. Pin a single price source in production.

### 0.3 Decimals lookup

```sql
SELECT decimals FROM tokens.erc20
WHERE blockchain = :chain AND contract_address = :token
```

### 0.4 Scaled-balance getter (rebasing aTokens / spTokens)

Aave v3 and SparkLend emit `Transfer` events with **mixed semantics** (verified by the POC — see §12):

| Code path | `Transfer(from, to, value)` value | Why |
|---|---|---|
| Mint (deposit) | **NOMINAL** — `amount + balanceIncrease` | AToken `_mintScaled` emits `Transfer(0x0, onBehalfOf, amountToMint)` with nominal total (deposit + accrued interest) |
| Burn (withdraw) | **NOMINAL** — `amount - balanceIncrease` | Same pattern in `_burnScaled` |
| User-to-user transfer | **SCALED** — `amount.rayDiv(index)` | AToken `_transfer` scales the input and calls `super._transfer(..., scaledAmount)` which emits `Transfer(from, to, scaledAmount)` |

Summing `tokens.transfers` therefore gives the **nominal balance at the time of the last event**, which drifts behind the current balance by the interest accrued since (the POC measured $166 RLUSD on a $208M aHorRwaRLUSD position → 0.001% drift, and $202 on $13.7M spUSDC → 0.001%).

The correct primitives are the dedicated AToken events (`atoken_evt_mint`, `atoken_evt_burn`, `atoken_evt_balancetransfer`) combined with the current `liquidityIndex` from `pool_evt_reservedataupdated`.

Per-event scaled-delta formulae (verified against Aave v3 source):

| Event | `value` semantics | Scaled delta |
|---|---|---|
| `Mint` | nominal (= deposit + accrued interest) | `(value − balanceIncrease) × 1e27 / index` |
| `Burn` | nominal (= withdraw − accrued interest) | `−(value + balanceIncrease) × 1e27 / index` |
| `BalanceTransfer` | **scaled** (= nominal × 1e27 / index) | `±value` (sign depends on `from`/`to`) |

Live liquidity index:

- `Pool_evt_ReserveDataUpdated(reserve, liquidityIndex, ...)` — one event per interest-rate update, pre-indexed in `aave_v3_{chain}.Pool_evt_ReserveDataUpdated` (variable by chain). The `liquidityIndex` column is RAY-scaled (1e27).

Balance at a specific block:

```
balance_nominal = scaled_balance × liquidityIndex_at(block) / 1e27
```

Validated template (works — used in POC for Aave Horizon aHorRwaRLUSD, match to Python within 0.001%):

```sql
WITH
mints AS (
  SELECT SUM(
    (CAST(value AS DOUBLE) - CAST(balanceIncrease AS DOUBLE)) * 1e27 / CAST(index AS DOUBLE)
  ) AS scaled_sum
  FROM aave_horizon_ethereum.atoken_evt_mint
  WHERE contract_address = :atoken
    AND onBehalfOf = :alm
    AND evt_block_number <= :snapshot_block
),
burns AS (
  SELECT SUM(
    -(CAST(value AS DOUBLE) + CAST(balanceIncrease AS DOUBLE)) * 1e27 / CAST(index AS DOUBLE)
  ) AS scaled_sum
  FROM aave_horizon_ethereum.atoken_evt_burn
  WHERE contract_address = :atoken
    AND "from" = :alm
    AND evt_block_number <= :snapshot_block
),
bt_in AS (
  SELECT SUM(CAST(value AS DOUBLE)) AS scaled_sum
  FROM aave_horizon_ethereum.atoken_evt_balancetransfer
  WHERE contract_address = :atoken
    AND "to" = :alm
    AND evt_block_number <= :snapshot_block
),
bt_out AS (
  SELECT SUM(-CAST(value AS DOUBLE)) AS scaled_sum
  FROM aave_horizon_ethereum.atoken_evt_balancetransfer
  WHERE contract_address = :atoken
    AND "from" = :alm
    AND evt_block_number <= :snapshot_block
),
scaled_balance AS (
  SELECT
    COALESCE((SELECT scaled_sum FROM mints), 0)
  + COALESCE((SELECT scaled_sum FROM burns), 0)
  + COALESCE((SELECT scaled_sum FROM bt_in), 0)
  + COALESCE((SELECT scaled_sum FROM bt_out), 0) AS scaled_raw
),
current_index AS (
  SELECT CAST(liquidityIndex AS DOUBLE) AS idx
  FROM aave_horizon_ethereum.poolinstance_evt_reservedataupdated
  WHERE reserve = :underlying
    AND evt_block_number <= :snapshot_block
  ORDER BY evt_block_number DESC
  LIMIT 1
)
SELECT scaled_raw * idx / 1e27 AS nominal_balance
FROM scaled_balance, current_index;
```

**Table-location note (POC-resolved):**
- Aave v3 core → `aave_v3_ethereum.*` (not verified in POC, but canonical per Dune catalog)
- Aave Horizon RWA market → `aave_horizon_ethereum.atoken_evt_{mint,burn,balancetransfer}` + `aave_horizon_ethereum.poolinstance_evt_reservedataupdated`
- SparkLend → `spark_protocol_ethereum.atoken_evt_{mint,burn,balancetransfer}` + `spark_protocol_ethereum.pool_evt_reservedataupdated`

Each aToken/spToken has its own `contract_address` in the shared decoded tables — filter by `contract_address = :atoken`.

---

## 1. Category A — Par stablecoin ERC-20

**Approach:** balance from §0.1, price hard-coded to $1.

```sql
WITH balance AS (
  SELECT
    SUM(CASE WHEN "to"   = :alm THEN CAST(amount_raw AS int256) ELSE 0 END) -
    SUM(CASE WHEN "from" = :alm THEN CAST(amount_raw AS int256) ELSE 0 END) AS raw
  FROM tokens.transfers
  WHERE blockchain       = :chain
    AND contract_address = :token
    AND block_date      <= DATE :snapshot_date
    AND ("to" = :alm OR "from" = :alm)
)
SELECT raw / POWER(10, (SELECT decimals FROM tokens.erc20
                        WHERE blockchain = :chain AND contract_address = :token))
       * 1.00 AS value_usd
FROM balance;
```

**Caveats:**
- `USDe` drifts ±0.5%. If MtM precision matters, swap the `1.00` for a `prices.minute` join.
- Stablecoin depegs (USDC Mar-2023, USDe briefly) are not captured by this approach.

### 1.a Why the par-stable shortcut, even though oracles exist

`docs/pricing/allocation_pricing.csv` lists Chainlink + Chronicle (+ Pyth /
Redstone fallback) feeds for every token in `PAR_STABLE_SYMBOLS`
(`USDC, USDS, DAI, USDT, PYUSD, RLUSD, AUSD, USDe`). The pipeline does not
read those feeds; every par-stable is hardcoded to `$1.00`. Three reasons:

1. **The drift is below the settlement tolerance.** Each token here trades
   within ±0.5% of $1.00 over any month's settlement window. The largest
   historical sustained drift was post-depeg USDC in March 2023 (one-week
   event). The cost-basis invariant tolerance is 1% (Q6); below that, oracle
   prices add noise without signal.

2. **Cost.** Reading 8 oracles per chain per snapshot adds ~16 RPC calls per
   settlement run with no measurable accuracy gain.

3. **Symmetry with upstream Sky math.** The SSR yield engine is denominated
   in $-equivalent USDS, not in oracle-priced USDS. Using oracle prices for
   USDS here would create a USDS↔oracle drift artifact that's not actually
   part of the agent's PnL — only an oracle-implementation choice.

If a future depeg event lasts long enough to matter (settlement-grade
threshold: ~50bps × ~30 days), drop the symbol from `PAR_STABLE_SYMBOLS` in
`src/settle/normalize/prices.py` and add a Source impl that reads the
relevant oracle.

---

## 2. Category B — ERC-4626 vault share

**Approach:** balance from §0.1 + per-vault share price derivation.

Dune has **no generic `erc4626.share_price` spell**. Options ranked by accuracy:

### 2.a Event-driven price (strongest, when events carry both sides)

ERC-4626 `Deposit(caller, owner, assets, shares)` and `Withdraw(caller, receiver, owner, assets, shares)` both emit `assets` and `shares`. A point-in-time share price is `assets / shares` from the latest event:

```sql
WITH events AS (
  SELECT evt_block_time, assets, shares
  FROM erc4626_ethereum.vault_evt_deposit
  WHERE contract_address = :vault AND evt_block_date <= DATE :snapshot_date
  UNION ALL
  SELECT evt_block_time, assets, shares
  FROM erc4626_ethereum.vault_evt_withdraw
  WHERE contract_address = :vault AND evt_block_date <= DATE :snapshot_date
)
SELECT assets * 1.0 / shares AS pps
FROM events
ORDER BY evt_block_time DESC
LIMIT 1;
```

**Pending question:** the generic `erc4626_ethereum.vault_evt_deposit` spell may not cover every MetaMorpho / Spark-curated / bbq vault. Need to confirm per-vault:
- **sUSDS** → custom Sky event model, likely under `sky_ethereum.*`
- **syrupUSDC / syrupUSDT** → Maple v2 emits `Deposit`/`Redeem`; decoded contracts likely under `maple_v2_ethereum.*`
- **fsUSDS** → Spark farming vault; schema unclear
- **sparkUSDC (Base)**, **grove-bbqUSDC**, **steakUSDC** → MetaMorpho ERC-4626, usually under `metamorpho_{chain}.*`
- **sparkPrimeUSDC1** → MetaMorpho variant
- **sUSDe** → Ethena vault, `ethena_ethereum.StakedUSDeV2_evt_*`

→ **Blocker Q2 — enumerate decoded-table coverage per vault.**

### 2.b TotalAssets / TotalSupply from storage-state events

Some vaults emit `TotalAssetsUpdated(totalAssets)` or a similar event. If available, pair with `Transfer` events (share total supply = sum of mints − burns). Per-vault investigation required.

### 2.c Approximation (worst case): count deposits, assume monotone yield

If no event carries both `assets` and `shares`, fall back to tracking cumulative deposit assets ÷ cumulative mint shares. This is a low-quality estimate — use only when 2.a/2.b are unavailable.

### 2.d Valuation wrap

```
value_usd = (shares_raw / 10^share_decimals) × pps × underlying_price
```

---

## 3. Category C — Aave v3 aToken (rebasing)

**Approach:** scaled-balance from §0.4 × `liquidityIndex` × underlying price.

```sql
-- Reuse §0.4 scaled_balance + latest_index template
-- Then:
SELECT
  (scaled_raw * liquidityIndex / 1e27) / POWER(10, underlying_decimals)
  * underlying_price_usd AS value_usd
FROM ...
```

**underlying_price_usd** — pull from `prices.minute` on the underlying's `contract_address` (not on the aToken itself, since aTokens have no trading price).

**Aave Horizon RWA (aHorRwaRLUSD, aHorRwaUSDC):** Horizon uses its own schema — `aave_horizon_ethereum.atoken_evt_{mint,burn,balancetransfer}` and `aave_horizon_ethereum.poolinstance_evt_reservedataupdated`. Distinct from the canonical `aave_v3_ethereum.*`.

### 3.a Cheap approximation — naive `tokens.transfers` sum

Summing `tokens.transfers` for an aToken gives the **nominal balance at the time of the last event**, not the current balance. From the POC: drift was $167 RLUSD on $208M (0.0008%) and $202 USDC on $13.8M (0.0015%). If that precision is acceptable, a one-line `SUM(CASE ...)` over `tokens.transfers` is viable. For monthly PnL snapshots on large books, prefer §3 proper method.

---

## 4. Category D — SparkLend spToken

**Approach:** identical to Category C.

Decoded tables (POC-verified):
- `spark_protocol_ethereum.atoken_evt_mint`
- `spark_protocol_ethereum.atoken_evt_burn`
- `spark_protocol_ethereum.atoken_evt_balancetransfer`
- `spark_protocol_ethereum.pool_evt_reservedataupdated`

Same scaled-balance × `liquidityIndex` math as §3. POC run on spUSDC @ Spark ETH ALM matched Python to within 0.0002%.

**Blocker Q5 — duplicate spDAI contracts.** `ASSET_CATALOG.md` flagged two live spDAI addresses (`0x73e65d...` and `0x4dedf26...`). The POC used spUSDC to avoid this. Before valuing spDAI, determine whether both are live (double-count risk) or one is deprecated.

---

## 5. Category E — RWA tranche / issuer token

**POC finding:** JTRSY exposes no on-chain price-getter (`pricePerShare`, `sharePrice`, `latestAnswer`, `convertToAssets` — all revert). Balance is trivial; price is the problem.

**Interim approach:** balance from §0.1 × off-chain NAV. Three candidate NAV sources, ranked by trust:

| Source | Timeliness | Authority | Dune-integrable | POC result for JTRSY |
|---|---|---|---|---|
| Issuer API (Centrifuge, BlackRock, Superstate) | daily | authoritative | via CSV upload | not attempted |
| CoinGecko listing | minutes | market-implied (thin volume for RWA tokens) | **not** Dune-integrable | $1.10/unit |
| `$1.00` placeholder | — | wrong by design | trivial | $1.00/unit |

**POC-measured impact for JTRSY @ Grove ETH ALM (1.323B units):**
- Placeholder ($1.00): $1,323,108,862
- CoinGecko ($1.10): $1,455,419,748
- **Δ = +$132M (+10%)** — the placeholder massively understates the book.

**Recommendation:** build a materialized Dune table `sky_msc.rwa_nav_daily` with columns `(snapshot_date, token_address, nav_per_unit_usd, source)`, seeded from:
- BlackRock → BUIDL-I
- Centrifuge API → JAAA, JTRSY
- Superstate API → USTB, USCC
- Placeholder `1.00` for STAC until resolved

All Category E valuations join against this table. Until it's built, Dune has no path to true NAV; Python (hitting the issuer API or CoinGecko) is the only option.

---

## 6. Category F — LP / pool share token

### 6.a Curve stableswap (`sUSDSUSDT`, `PYUSDUSDS`)

**POC-validated approach (Python):** call `pool.get_virtual_price()`, `pool.balances(i)`, `pool.totalSupply()`, and the underlying's price-getter (e.g. `convertToAssets` for yield-bearing coins like sUSDS).

Two methods, compared in the POC on Spark ETH ALM's sUSDSUSDT position ($50M, 99.97% of the pool):

| Method | Formula | POC value | Accuracy |
|---|---|---:|---|
| **A — virtual_price shortcut** | `lp_balance × virtual_price / 1e18` | $49,998,805 | Assumes pool base unit ≈ $1; wrong for pools with non-peg-holding yield-bearing coins |
| **B — reserves reconstruction** | `share × Σ balance[i] × price[i]` where `share = lp_balance / totalSupply` | $50,000,791 | Uses live underlying prices; B − A = $1,986 (0.004%) on a $50M position — attributable to `virtual_price` lagging sUSDS interest accrual |

**Recommendation:** use Method B. `virtual_price` is cheap but incorporates the pool's internal invariant view, which lags when one coin accrues yield externally (like sUSDS).

**Dune path:** reconstruct `balance[i]` and `totalSupply` from `curvefi_ethereum.*_evt_{AddLiquidity,RemoveLiquidity,TokenExchange}` per pool, plus LP `Transfer` events. Bespoke per pool. Alternatively, encode the pool state as events at each `block_date` via a materialized spell.

→ **Blocker Q7 — pre-indexed Curve pool-state spell** (still unresolved). For single-day valuations, Python is dramatically simpler than Dune reconstruction; for daily timeseries, the per-pool Dune work pays off.

### 6.b Uniswap V3 position NFT (deferred Grove E12)

Not yet observed in ALM transfers, but documented Grove venue. Requires:
- `nonfungiblePositionManager.positions(tokenId)` → `tickLower`, `tickUpper`, `liquidity`
- `pool.slot0().sqrtPriceX96`
- Compute token0/token1 amounts via UniV3 math, then price each

Dune tables: `uniswap_v3_ethereum.positions`, `uniswap_v3_ethereum.Pool_evt_Swap`. No spell pre-computes position value at a given date.

→ **Blocker Q8 — when Uni V3 venue becomes active, build a dedicated position-pricer query.**

---

## 7. Category G — Native / wrapped gas

**WETH, WMON** — ERC-20, use §0.1 balance × `prices.minute` price.

**ETH, AVAX, MON native** — not ERC-20; no `tokens.transfers` entry. Sources:

- `{chain}.traces` — sum `value` where `to = :alm` or `from = :alm` (+gas-payer for outbound). Expensive on Ethereum.
- Alternative: `{chain}.transactions` for direct `value` transfers + `{chain}.traces` for internal.

For the volumes involved (Grove AVAX gas funding ~$2M total across two years), native-ETH/AVAX precision is low-stakes. Recommendation: **skip native, track only wrapped WETH/WMON** for the PnL view. Document gas spend separately if needed.

→ **Blocker Q9 — decide whether gas PnL is in-scope for MSC** or out-of-scope operational cost.

---

## 8. Category H — Governance / reward token (MORPHO)

**Approach:** balance from §0.1 × `prices.minute`.

```sql
SELECT
  (balance_raw / POWER(10, 18))
  * (SELECT price FROM prices.minute
     WHERE blockchain = 'base' AND contract_address = :morpho_token
       AND timestamp >= TIMESTAMP :snapshot_ts - INTERVAL '12' HOUR
       AND timestamp <= TIMESTAMP :snapshot_ts
     ORDER BY timestamp DESC LIMIT 1)
  AS value_usd
```

Low materiality (observed flows ≤$6M base, smaller elsewhere). Safe to track coarsely. POC matched Python to within a $0.008 price-source gap (Dune $1.91 vs CoinGecko $1.92).

---

## 9. Open questions / blockers

Updated after the POC (see §12 and [valuation_poc/COMPARISON.md](valuation_poc/COMPARISON.md)).

| # | Status | Blocker | Category | Impact |
|---|---|---|---|---|
| Q1 | ✅ **Resolved** | Aave v3 schema is per-project (e.g. `aave_horizon_ethereum.atoken_evt_*`) with one row per `contract_address`. Filter on `contract_address = :atoken`. | C | — |
| Q2 | ⚠ Open | Decoded-table coverage per ERC-4626 vault (sUSDS, syrupUSDC/T, fsUSDS, sparkUSDC-vault, steakUSDC, grove-bbq*, sUSDe, sparkPrimeUSDC1) | B | For single-day snapshots, Python `convertToAssets` is simpler; for daily timeseries, still need to enumerate per-vault decoded tables |
| Q3 | ✅ **Resolved** | Horizon tables live under `aave_horizon_ethereum.*`. Pool event is `poolinstance_evt_reservedataupdated` (note the `instance` infix). | C | — |
| Q4 | ✅ **Resolved** | SparkLend → `spark_protocol_ethereum.atoken_evt_{mint,burn,balancetransfer}` + `spark_protocol_ethereum.pool_evt_reservedataupdated` | D | — |
| Q5 | ⚠ Open | Two spDAI contracts (`0x73e65d...` v1 + `0x4dedf26...` v2?) — which is live? | D | Avoid double-counting |
| Q6 | ⚠ Partially resolved | RWA NAV: JTRSY reverts on all on-chain getters. CoinGecko has JTRSY @ $1.10 and JAAA @ $1.031 (10%/3% above the $1 placeholder). Still need materialized `sky_msc.rwa_nav_daily` seeded from issuer APIs to be authoritative and Dune-visible. | E | Largest known MtM understatement — POC showed +$132M on Grove's JTRSY alone |
| Q7 | ⚠ Open | Pre-indexed Curve pool-state spell — not found. Options remain: (a) per-pool Dune reconstruction from events, or (b) Python RPC path (POC path). POC on sUSDSUSDT worked in Python in ~10 RPC calls. | F | Python viable today; Dune path is bespoke per pool |
| Q8 | ⚠ Open (deferred) | Uniswap V3 position-pricer — no live ALM position yet | F | Out of scope until Grove E12 activates |
| Q9 | ⚠ Open | Native-gas PnL in/out of MSC scope? | G | POC showed Grove ETH ALM has $117 ETH; immaterial for PnL. Recommend: exclude native from position-value, track separately as operational cost |
| Q10 | ✅ **Resolved** | `prices.minute` column is `timestamp` (not `minute`) — confirmed by initial POC failure. Always narrow by `timestamp >= ... AND timestamp <= ...` or Dune hits memory cap. | A/G/H | — |
| Q11 | ✅ **Resolved** | Snapshot convention: use `block_number` cutoff for exact Dune↔Python alignment. Using `block_date` admits sub-day drift. | All | Adopt in all future queries |
| Q12 | ⚠ Open | Plume + Monad asset coverage in `tokens.transfers` — per [grove/QUESTIONS.md](grove/QUESTIONS.md) Q4 | A–F | Many assets simply missing on Dune; Python RPC works if per-chain RPC is available |

## 10. Build order (recommended, updated post-POC)

1. ~~**Q11 first.**~~ ✅ Resolved — use `block_number` cutoffs.
2. **Categories A + G + H** — straightforward, use `tokens.transfers` + `prices.minute`. A is trivial (stablecoin × $1). H works within $0.01 of CoinGecko. G: exclude native from PnL per Q9 recommendation.
3. **Category C + D** (Aave + SparkLend). Use the scaled-balance × `liquidityIndex` template from §0.4; POC-validated to 0.001% accuracy against Python.
4. **Category B.** Python `convertToAssets` is the canonical path for single-day snapshots. Dune is only worth the dig if we need daily timeseries (then resolve Q2 vault-by-vault).
5. **Category F** (Curve). Python Method B (reserves × prices) is POC-validated. Dune reconstruction from per-pool events is bespoke and only worth it for timeseries.
6. **Category E.** Blocked on Q6 (RWA NAV feed infra). Until then, Python + CoinGecko is the least-bad interim; placeholder $1 is a ~10% understatement at current JTRSY price.

## 11. Dune vs Python decision tree

Based on POC findings:

| When | Prefer | Why |
|---|---|---|
| Single snapshot (one-off reconciliation, ops check) | **Python (RPC + CoinGecko + issuer API)** | One eth_call per asset; bit-exact; no schema archaeology |
| Daily / historical timeseries | **Dune** | `tokens.transfers` + decoded events cover years of history at low cost; Python would require a historical-RPC pull per day |
| Rebasing tokens (aTokens, spTokens) | Either | Dune scaled-events method is POC-validated; Python `balanceOf` is simpler |
| RWA tokens | Python (until Q6) | No on-chain NAV; Dune has no NAV feed; Python can call issuer APIs |
| Curve/Uni V3 LP | Python for single-day; Dune for timeseries | Pool state reconstruction on Dune is bespoke; RPC gives it in one call |

## 12. POC validation — summary of Dune↔Python convergence

See [valuation_poc/COMPARISON.md](valuation_poc/COMPARISON.md) for the full run. Condensed:

| Cat | Asset | Python | Dune | Δ |
|---|---|---:|---:|---:|
| A | PYUSD @ Spark ETH | $677,206,361.90 | $677,206,361.90 | 0.000% |
| C | aHorRwaRLUSD @ Grove ETH | $207,940,911.97 | $207,939,438.97 (M2) / $207,939,272.03 (M1) | 0.0007% / 0.0008% |
| D | spUSDC @ Spark ETH | $13,774,683.13 | $13,774,654.50 | 0.0002% |
| H | MORPHO @ Spark Base | $1.549 | $1.541 | price-source gap only |
| B | sUSDS @ Spark ETH | $1,492,780,950.92 | — (Python only) | |
| E | JTRSY @ Grove ETH | $1,455,419,748 (@CoinGecko $1.10) vs $1,323,108,862 (@$1 placeholder) | — (Python only) | +$132M if NAV-aware |
| F | sUSDSUSDT LP @ Spark ETH | $50,000,791 (reserves method) vs $49,998,805 (virtual_price) | — (Python only) | Method A vs B Δ = 0.004% |
| G | native ETH @ Grove ETH | $117 | — (non-ERC-20) | immaterial |

**Methodology conclusions:**
- Par-stable and rebasing aToken/spToken valuations on Dune match Python to within 0.001%; difference is purely block-alignment (Python ran "latest", Dune used the block-number cutoff).
- Methods M1 (naive `tokens.transfers` sum) and M2 (scaled events × `liquidityIndex`) for aTokens differ by ~0.001% — acceptable approximation if Dune-only and no decoded events are available, but M2 is the correct primitive.
- Price-source choice matters even for liquid tokens (Dune vs CoinGecko gap of 0.5% on MORPHO at a single moment).
