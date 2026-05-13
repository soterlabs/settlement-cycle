-- PSM3 pool-wide total-shares history per event-block.
--
-- Same shape as ``psm3_shares_history.sql`` but with no ``holder`` filter —
-- aggregates EVERY deposit/withdraw against the pool to track cumulative
-- ``totalShares`` over time. Used by ``DunePsm3Source.convert_to_asset_value``
-- to compute the on-chain rate (``shares × totalAssets / totalShares``)
-- without an RPC ``convertToAssetValue`` call.
--
-- Parameters:
--   {{chain}}      text       — Dune chain ('arbitrum', 'base',
--                               'optimism', 'unichain').
--   {{pin_block}}  number     — upper bound on event block_number.
--
-- Output: one row per event-block (deposit OR withdraw, any holder).
--   block_number, block_time, delta_shares, cum_total_shares

WITH all_events AS (
    SELECT
        evt_block_number AS block_number,
        evt_block_time   AS block_time,
        evt_index,
        CAST(sharesMinted AS DECIMAL(38, 0))  AS delta_shares
    FROM spark_protocol_multichain.psm3_evt_deposit
    WHERE chain = '{{chain}}'
      AND evt_block_number <= {{pin_block}}
    UNION ALL
    SELECT
        evt_block_number,
        evt_block_time,
        evt_index,
        -CAST(sharesBurned AS DECIMAL(38, 0)) AS delta_shares
    FROM spark_protocol_multichain.psm3_evt_withdraw
    WHERE chain = '{{chain}}'
      AND evt_block_number <= {{pin_block}}
)
SELECT
    block_number,
    block_time,
    delta_shares,
    SUM(delta_shares) OVER (ORDER BY block_number, evt_index) AS cum_total_shares
FROM all_events
ORDER BY block_number, evt_index
