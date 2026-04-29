-- Daily net token flow into/out of a single holder address.
--
-- Parameters:
--   {{chain}}                  text       — e.g. 'ethereum', 'base'
--   {{token}}                  varbinary  — token contract address
--   {{holder}}                 varbinary  — holder address (subproxy or ALM)
--   {{start_date}}             text       — 'YYYY-MM-DD'
--   {{pin_block}}              number     — upper-bound block_number cutoff
--   {{min_transfer_amount}}    number     — drop transfers strictly below this
--                                            amount (decimal-adjusted, USD-equiv
--                                            for par-stables). Pass 0 for no
--                                            filtering. Used by BUIDL E10 to
--                                            separate sub-$1M yield-distribution
--                                            mints from real capital deposits.
--
-- Output columns: block_date, daily_net, cum_balance
--
-- `daily_net` is signed: positive = inflow, negative = outflow. `cum_balance` is the
-- running balance held by `holder`. Note: `tokens.transfers.amount` is decimal-adjusted.

WITH flows AS (
  SELECT
    block_date,
    SUM(CASE WHEN "to"   = {{holder}} THEN amount ELSE 0 END) -
    SUM(CASE WHEN "from" = {{holder}} THEN amount ELSE 0 END) AS daily_net
  FROM tokens.transfers
  WHERE blockchain        = '{{chain}}'
    AND contract_address = {{token}}
    AND ("to" = {{holder}} OR "from" = {{holder}})
    AND block_date      >= DATE '{{start_date}}'
    AND block_number    <= {{pin_block}}
    AND amount          >= {{min_transfer_amount}}
  GROUP BY block_date
)
SELECT
  block_date,
  daily_net,
  SUM(daily_net) OVER (ORDER BY block_date) AS cum_balance
FROM flows
ORDER BY block_date
