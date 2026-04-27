-- Category C — Aave v3 aToken (aHorRwaRLUSD @ Grove ETH ALM)
-- Cutoff: Ethereum block 24945607 (2026-04-23 22:22:23Z)
-- Expected (Python eth_call): 207,940,911.974729 RLUSD ≈ $207,940,911.97
--
-- TWO METHODS:
--   M1 (naive): sum tokens.transfers — mixes SCALED (user transfers) and NOMINAL
--               (mint/burn). Works only if no user-to-user transfers ever happened.
--   M2 (correct): reconstruct scaled balance from decoded Mint/Burn/BalanceTransfer
--               events × current liquidityIndex.
--
-- AToken event value semantics (Aave v3):
--   Mint.value           = NOMINAL minted (= deposit + auto-accrued interest)
--   Mint.balanceIncrease = auto-accrued portion; raw deposit = value - balanceIncrease
--   Burn.value           = NOMINAL burned (= withdrawal - auto-accrued interest)
--   Burn.balanceIncrease = auto-accrued portion; raw withdraw = value + balanceIncrease
--   BalanceTransfer.value = SCALED amount (= nominal × RAY / index)
--
-- Scaled delta per event:
--   Mint:             (value - balanceIncrease) × 1e27 / index
--   Burn:            -(value + balanceIncrease) × 1e27 / index
--   BalanceTransfer:  ±value

WITH
-- ---- M1: naive tokens.transfers sum ----
m1_naive AS (
  SELECT
    SUM(CASE WHEN "to"   = 0x491edfb0b8b608044e227225c715981a30f3a44e THEN amount ELSE 0 END) -
    SUM(CASE WHEN "from" = 0x491edfb0b8b608044e227225c715981a30f3a44e THEN amount ELSE 0 END) AS nominal_sum
  FROM tokens.transfers
  WHERE blockchain = 'ethereum'
    AND contract_address = 0xe3190143eb552456f88464662f0c0c4ac67a77eb
    AND ("to"   = 0x491edfb0b8b608044e227225c715981a30f3a44e
      OR "from" = 0x491edfb0b8b608044e227225c715981a30f3a44e)
    AND block_number <= 24945607
),

-- ---- M2: scaled balance from decoded events ----
mints AS (
  SELECT SUM(
    (CAST(value AS DOUBLE) - CAST(balanceIncrease AS DOUBLE))
    * 1e27 / CAST(index AS DOUBLE)
  ) AS scaled_sum
  FROM aave_horizon_ethereum.atoken_evt_mint
  WHERE contract_address = 0xe3190143eb552456f88464662f0c0c4ac67a77eb
    AND onBehalfOf = 0x491edfb0b8b608044e227225c715981a30f3a44e
    AND evt_block_number <= 24945607
),
burns AS (
  SELECT SUM(
    -(CAST(value AS DOUBLE) + CAST(balanceIncrease AS DOUBLE))
    * 1e27 / CAST(index AS DOUBLE)
  ) AS scaled_sum
  FROM aave_horizon_ethereum.atoken_evt_burn
  WHERE contract_address = 0xe3190143eb552456f88464662f0c0c4ac67a77eb
    AND "from" = 0x491edfb0b8b608044e227225c715981a30f3a44e
    AND evt_block_number <= 24945607
),
bt_in AS (
  SELECT SUM(CAST(value AS DOUBLE)) AS scaled_sum
  FROM aave_horizon_ethereum.atoken_evt_balancetransfer
  WHERE contract_address = 0xe3190143eb552456f88464662f0c0c4ac67a77eb
    AND "to" = 0x491edfb0b8b608044e227225c715981a30f3a44e
    AND evt_block_number <= 24945607
),
bt_out AS (
  SELECT SUM(-CAST(value AS DOUBLE)) AS scaled_sum
  FROM aave_horizon_ethereum.atoken_evt_balancetransfer
  WHERE contract_address = 0xe3190143eb552456f88464662f0c0c4ac67a77eb
    AND "from" = 0x491edfb0b8b608044e227225c715981a30f3a44e
    AND evt_block_number <= 24945607
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
  WHERE reserve = 0x8292bb45bf1ee4d140127049757c2e0ff06317ed  -- RLUSD
    AND evt_block_number <= 24945607
  ORDER BY evt_block_number DESC
  LIMIT 1
)
SELECT
  m1.nominal_sum                                          AS method_1_naive_value_usd,
  sb.scaled_raw                                           AS scaled_balance_raw,
  ci.idx                                                  AS current_liquidity_index_raw,
  sb.scaled_raw * ci.idx / 1e27 / 1e18                    AS balance_rlusd_units,
  sb.scaled_raw * ci.idx / 1e27 / 1e18 * 1.00             AS method_2_correct_value_usd,
  (sb.scaled_raw * ci.idx / 1e27 / 1e18) - m1.nominal_sum AS m2_minus_m1_drift_rlusd
FROM m1_naive m1, scaled_balance sb, current_index ci;
