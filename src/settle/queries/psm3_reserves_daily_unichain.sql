-- Dune parity oracle: same transfer running sum as psm3_reserves_history,
-- reduced to the final state per token/day before API export.
-- Keeps block/log ordering; avoids exporting millions of event rows.
WITH flows AS (
    SELECT
        contract_address AS token,
        evt_block_number AS block_number,
        evt_block_time   AS block_time,
        evt_index,
        CASE
            WHEN "to"   = {{psm3}} THEN  CAST(value AS DECIMAL(38, 0))
            WHEN "from" = {{psm3}} THEN -CAST(value AS DECIMAL(38, 0))
            ELSE CAST(0 AS DECIMAL(38, 0))
        END AS delta_raw
    FROM erc20_unichain.evt_transfer
    WHERE evt_block_date >= DATE '{{start_month}}'
      AND contract_address IN ({{usdc}}, {{usds}}, {{susds}})
      AND ("to" = {{psm3}} OR "from" = {{psm3}})
      AND evt_block_number <= {{pin_block}}
), balances AS (
SELECT
    token,
    block_number,
    block_time,
    evt_index,
    SUM(delta_raw) OVER (PARTITION BY token ORDER BY block_number, evt_index) AS cum_balance_raw
FROM flows
 )
SELECT token, CAST(block_time AS DATE) AS block_date,
       MAX_BY(cum_balance_raw, ROW(block_number, evt_index)) AS cum_balance_raw
FROM balances
GROUP BY token, CAST(block_time AS DATE)
ORDER BY token, block_date
