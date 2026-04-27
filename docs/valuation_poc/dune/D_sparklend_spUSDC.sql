-- Category D — SparkLend spToken (spUSDC @ Spark ETH ALM)
-- Cutoff: Ethereum block 24945607 (2026-04-23 22:22:23Z)
-- Expected (Python eth_call): 13,774,683.126376 USDC ≈ $13,774,683.13
--
-- SparkLend is an Aave v3 fork. Same event schema: atoken_evt_mint/burn/balancetransfer.
-- Pool table: spark_protocol_ethereum.pool_evt_reservedataupdated

WITH
-- ---- M1: naive tokens.transfers sum ----
m1_naive AS (
  SELECT
    SUM(CASE WHEN "to"   = 0x1601843c5e9bc251a3272907010afa41fa18347e THEN amount ELSE 0 END) -
    SUM(CASE WHEN "from" = 0x1601843c5e9bc251a3272907010afa41fa18347e THEN amount ELSE 0 END) AS nominal_sum
  FROM tokens.transfers
  WHERE blockchain = 'ethereum'
    AND contract_address = 0x377c3bd93f2a2984e1e7be6a5c22c525ed4a4815
    AND ("to"   = 0x1601843c5e9bc251a3272907010afa41fa18347e
      OR "from" = 0x1601843c5e9bc251a3272907010afa41fa18347e)
    AND block_number <= 24945607
),

-- ---- M2: scaled balance from decoded events ----
mints AS (
  SELECT SUM(
    (CAST(value AS DOUBLE) - CAST(balanceIncrease AS DOUBLE))
    * 1e27 / CAST(index AS DOUBLE)
  ) AS scaled_sum
  FROM spark_protocol_ethereum.atoken_evt_mint
  WHERE contract_address = 0x377c3bd93f2a2984e1e7be6a5c22c525ed4a4815
    AND onBehalfOf = 0x1601843c5e9bc251a3272907010afa41fa18347e
    AND evt_block_number <= 24945607
),
burns AS (
  SELECT SUM(
    -(CAST(value AS DOUBLE) + CAST(balanceIncrease AS DOUBLE))
    * 1e27 / CAST(index AS DOUBLE)
  ) AS scaled_sum
  FROM spark_protocol_ethereum.atoken_evt_burn
  WHERE contract_address = 0x377c3bd93f2a2984e1e7be6a5c22c525ed4a4815
    AND "from" = 0x1601843c5e9bc251a3272907010afa41fa18347e
    AND evt_block_number <= 24945607
),
bt_in AS (
  SELECT SUM(CAST(value AS DOUBLE)) AS scaled_sum
  FROM spark_protocol_ethereum.atoken_evt_balancetransfer
  WHERE contract_address = 0x377c3bd93f2a2984e1e7be6a5c22c525ed4a4815
    AND "to" = 0x1601843c5e9bc251a3272907010afa41fa18347e
    AND evt_block_number <= 24945607
),
bt_out AS (
  SELECT SUM(-CAST(value AS DOUBLE)) AS scaled_sum
  FROM spark_protocol_ethereum.atoken_evt_balancetransfer
  WHERE contract_address = 0x377c3bd93f2a2984e1e7be6a5c22c525ed4a4815
    AND "from" = 0x1601843c5e9bc251a3272907010afa41fa18347e
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
  FROM spark_protocol_ethereum.pool_evt_reservedataupdated
  WHERE reserve = 0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48  -- USDC
    AND evt_block_number <= 24945607
  ORDER BY evt_block_number DESC
  LIMIT 1
)
SELECT
  m1.nominal_sum                                         AS method_1_naive_value_usd,
  sb.scaled_raw                                          AS scaled_balance_raw,
  ci.idx                                                 AS current_liquidity_index_raw,
  sb.scaled_raw * ci.idx / 1e27 / 1e6                    AS balance_usdc_units,
  sb.scaled_raw * ci.idx / 1e27 / 1e6 * 1.00             AS method_2_correct_value_usd,
  (sb.scaled_raw * ci.idx / 1e27 / 1e6) - m1.nominal_sum AS m2_minus_m1_drift_usdc
FROM m1_naive m1, scaled_balance sb, current_index ci;
