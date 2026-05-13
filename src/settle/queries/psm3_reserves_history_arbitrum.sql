-- PSM3 pool reserves per token per event-block (cumulative) on Arbitrum.
--
-- See the sibling files for Base / Optimism / Unichain — same body, only
-- the FROM table changes. Dune doesn't support ``{{param}}`` substitution
-- in FROM identifiers, so the per-chain split is the cleanest path.
--
-- We use the chain-scoped ``erc20_arbitrum.evt_transfer`` spell rather than
-- the multi-chain ``tokens.transfers`` spell. The latter scans every chain
-- in the universe and 402s on Dune's ``community_fluid_engine`` plan;
-- the per-chain spell is partitioned narrowly enough to fit.
--
-- Parameters:
--   {{psm3}}         varbinary  — PSM3 contract address on this chain.
--   {{usdc}}         varbinary  — USDC contract.
--   {{usds}}         varbinary  — USDS contract.
--   {{susds}}        varbinary  — sUSDS contract.
--   {{start_month}}  text       — 'YYYY-MM-01' partition floor.
--   {{pin_block}}    number     — upper-bound block_number.
--
-- Output: one row per (token, transfer-event-block):
--   token, block_number, block_time, cum_balance_raw
-- where ``cum_balance_raw`` is the uint256 token-units balance (NOT
-- divided by decimals). Python source uses it directly.

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
    FROM erc20_arbitrum.evt_transfer
    WHERE evt_block_date >= DATE '{{start_month}}'
      AND contract_address IN ({{usdc}}, {{usds}}, {{susds}})
      AND ("to" = {{psm3}} OR "from" = {{psm3}})
      AND evt_block_number <= {{pin_block}}
)
SELECT
    token,
    block_number,
    block_time,
    SUM(delta_raw) OVER (PARTITION BY token ORDER BY block_number, evt_index) AS cum_balance_raw
FROM flows
ORDER BY token, block_number, evt_index
