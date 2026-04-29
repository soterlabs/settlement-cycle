-- Daily directed token flow from one address to another (cost-basis input).
--
-- Parameters:
--   {{chain}}        text
--   {{token}}        varbinary  — underlying token (e.g. USDC)
--   {{from_addr}}    varbinary  — typically the ALM proxy
--   {{to_addr}}      varbinary  — typically the venue contract
--   {{start_date}}   text       — 'YYYY-MM-DD'
--   {{pin_block}}    number     — upper-bound block_number cutoff
--
-- Output columns: block_date, daily_inflow, cum_inflow
--
-- Used by Compute to track cost basis: cumulative `from_addr → to_addr` deposits.

WITH flows AS (
  SELECT
    block_date,
    SUM(amount) AS daily_inflow
  FROM tokens.transfers
  WHERE blockchain        = '{{chain}}'
    AND contract_address = {{token}}
    AND "from"            = {{from_addr}}
    AND "to"              = {{to_addr}}
    AND block_date      >= DATE '{{start_date}}'
    AND block_number    <= {{pin_block}}
  GROUP BY block_date
)
SELECT
  block_date,
  daily_inflow,
  SUM(daily_inflow) OVER (ORDER BY block_date) AS cum_inflow
FROM flows
ORDER BY block_date
