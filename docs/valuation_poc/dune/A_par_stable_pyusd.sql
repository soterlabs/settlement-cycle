-- Category A — Par stablecoin (PYUSD @ Spark ETH ALM)
-- Cutoff: Ethereum block 24945607 (2026-04-23 22:22:23Z)
-- Expected (Python eth_call): 677,206,361.897531 USD

WITH net_balance AS (
  SELECT
    SUM(CASE WHEN "to"   = 0x1601843c5e9bc251a3272907010afa41fa18347e THEN amount ELSE 0 END) -
    SUM(CASE WHEN "from" = 0x1601843c5e9bc251a3272907010afa41fa18347e THEN amount ELSE 0 END) AS balance_pyusd
  FROM tokens.transfers
  WHERE blockchain = 'ethereum'
    AND contract_address = 0x6c3ea9036406852006290770bedfcaba0e23a0e8
    AND ("to"   = 0x1601843c5e9bc251a3272907010afa41fa18347e
      OR "from" = 0x1601843c5e9bc251a3272907010afa41fa18347e)
    AND block_number <= 24945607
)
SELECT
  balance_pyusd                   AS balance_units,
  1.00                            AS unit_price_usd,
  balance_pyusd * 1.00            AS value_usd
FROM net_balance;
