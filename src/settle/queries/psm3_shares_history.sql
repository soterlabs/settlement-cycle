-- PSM3 per-event share-balance history for a given (chain, holder).
--
-- PSM3 shares are internal accounting (no ERC-20 Transfer event), but the
-- ``Deposit`` / ``Withdraw`` events emit ``sharesMinted`` / ``sharesBurned``
-- alongside the ``receiver`` (deposits) / ``user`` (withdrawals). Summing
-- these chronologically per holder reproduces the on-chain share balance
-- at any block — matching ``IPsm3Source.shares_of(chain, psm3, holder, block)``
-- without an RPC round-trip.
--
-- Used by ``DunePsm3Source`` to bulk-load the entire holder-balance
-- timeseries for a settlement period in one Dune query (instead of
-- ~31 days × 4 chains of per-day RPC calls that the Arbitrum drpc upstream
-- intermittently fails on).
--
-- Parameters:
--   {{chain}}      text       — Dune chain ('arbitrum', 'base',
--                               'optimism', 'unichain'). Must be one of the
--                               four where ``spark_protocol_multichain`` has
--                               PSM3 decoded events.
--   {{holder}}     varbinary  — ALM proxy address (hex, ``0x``-prefixed).
--   {{pin_block}}  number     — upper bound on event block_number
--                               (also part of the cache key).
--
-- Output: one row per event-block where the holder's shares changed.
--   block_number       evt_block_number of the deposit/withdraw
--   block_time         timestamp (ts) of that block
--   delta_shares       +sharesMinted on deposits, -sharesBurned on withdraws
--   cum_shares         running balance after this event (uint256-valued
--                      but exposed as a signed decimal to permit deltas)

WITH all_events AS (
    SELECT
        evt_block_number AS block_number,
        evt_block_time   AS block_time,
        evt_index,
        CAST(sharesMinted AS DECIMAL(38, 0))  AS delta_shares
    FROM spark_protocol_multichain.psm3_evt_deposit
    WHERE chain = '{{chain}}'
      AND receiver = {{holder}}
      AND evt_block_number <= {{pin_block}}
    UNION ALL
    SELECT
        evt_block_number,
        evt_block_time,
        evt_index,
        -CAST(sharesBurned AS DECIMAL(38, 0)) AS delta_shares
    FROM spark_protocol_multichain.psm3_evt_withdraw
    WHERE chain = '{{chain}}'
      AND user = {{holder}}
      AND evt_block_number <= {{pin_block}}
)
SELECT
    block_number,
    block_time,
    delta_shares,
    SUM(delta_shares) OVER (ORDER BY block_number, evt_index) AS cum_shares
FROM all_events
ORDER BY block_number, evt_index
