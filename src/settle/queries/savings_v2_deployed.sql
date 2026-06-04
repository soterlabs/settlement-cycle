-- Daily sUSDS amount deployed from the Spark ALM into Savings V2 (spUSDC).
--
-- Parameters:
--   {{pin_block}}   number  — accepted by the pipeline framework but unused here;
--                             this table is a pre-aggregated dataset with no
--                             block-level column.
--
-- Output columns: dt (date string), susds_deployed_usd (float)
--
-- Source: dune.sparkdotfi.result_savings_v_2_deployment_metrics
-- Used to correct the overcounting in S32 (sUSDS raw / POL at Spark ETH ALM):
-- the ALM's sUSDS balance includes shares that have been deployed to Savings V2
-- and are therefore not truly held at the ALM proxy.

SELECT
    dt,
    deployed_amount AS susds_deployed_usd
FROM dune.sparkdotfi.result_savings_v_2_deployment_metrics
WHERE token_symbol = 'spUSDC'
  AND {{pin_block}} >= 0
ORDER BY dt
