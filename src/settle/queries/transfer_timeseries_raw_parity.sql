-- Parity oracle: same filters/grouping as transfer_timeseries.sql,
-- using integer amount_raw to separate event differences from DOUBLE rounding.
WITH flows AS (
  SELECT
    block_date,
    SUM(CASE WHEN "to"   = {{holder}} THEN CAST(amount_raw AS DECIMAL(38, 0)) ELSE 0 END) -
    SUM(CASE WHEN "from" = {{holder}} THEN CAST(amount_raw AS DECIMAL(38, 0)) ELSE 0 END) AS daily_net
  FROM tokens.transfers
  WHERE blockchain        = '{{chain}}'
    AND contract_address = {{token}}
    AND ("to" = {{holder}} OR "from" = {{holder}})
    AND block_date      >= DATE '{{start_date}}'
    AND block_number    <= {{pin_block}}
    AND CAST(amount_raw AS DECIMAL(38, 0))          >= {{min_transfer_raw}}
  GROUP BY block_date
)
SELECT
  block_date,
  daily_net,
  SUM(daily_net) OVER (ORDER BY block_date) AS cum_balance
FROM flows
ORDER BY block_date
