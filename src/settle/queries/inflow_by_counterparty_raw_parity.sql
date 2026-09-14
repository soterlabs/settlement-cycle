-- Parity oracle: same filters/grouping as inflow_by_counterparty.sql,
-- using integer amount_raw to separate event differences from DOUBLE rounding.
WITH directed AS (
  SELECT
    block_date,
    CASE WHEN "to" = {{holder}} THEN "from" ELSE "to" END AS counterparty,
    CASE WHEN "to" = {{holder}} THEN CAST(amount_raw AS DECIMAL(38, 0)) ELSE -CAST(amount_raw AS DECIMAL(38, 0)) END AS signed_amount
  FROM tokens.transfers
  WHERE blockchain        = '{{chain}}'
    AND contract_address = {{token}}
    AND ("to" = {{holder}} OR "from" = {{holder}})
    AND block_date      >= DATE '{{start_date}}'
    AND block_number    <= {{pin_block}}
)
SELECT
  block_date,
  counterparty,
  SUM(signed_amount) AS signed_amount
FROM directed
GROUP BY block_date, counterparty
ORDER BY block_date, counterparty
