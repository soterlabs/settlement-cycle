-- Parity oracle: same filters/grouping as venue_inflow.sql,
-- using integer amount_raw to separate event differences from DOUBLE rounding.
WITH flows AS (
  SELECT
    block_date,
    SUM(CAST(amount_raw AS DECIMAL(38, 0))) AS daily_inflow
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
