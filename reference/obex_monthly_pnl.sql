-- OBEX — Monthly PnL (combined)
-- Dune query ID: TBD (will be set after createDuneQuery)
--
-- Self-contained OBEX-specific rollup that fuses the two shared templates
-- (6954380 monthly_pnl + 6957966 monthly_agent_rate) with OBEX params hardcoded.
-- Output is one row per month with Sky revenue and OBEX revenue side-by-side.
--
-- OBEX params:
--   ilk_bytes32:         0x414c4c4f4341544f522d4f4245582d4100000000000000000000000000000000
--   subproxy_address:    0x8be042581f581E3620e29F213EA8b94afA1C8071
--   alm_proxy_address:   0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2
--   venue_token_address: 0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b (syrupUSDC)
--   start_date:          2025-11-01
--   calendar_start_date: 2025-11-17

WITH
-- ==========================================================================
-- A) Monthly PnL section (mirrors shared query 6954380)
-- ==========================================================================
frobs AS (
  SELECT tr.block_date,
    CAST(bytearray_to_int256(substr(tr.input, 165, 32)) AS DOUBLE) / 1e18 AS dart
  FROM ethereum.traces tr
  WHERE tr."to" = 0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B
    AND substr(tr.input, 1, 4) = 0x76088703
    AND substr(tr.input, 5, 32) = 0x414c4c4f4341544f522d4f4245582d4100000000000000000000000000000000
    AND tr.success = true AND tr.block_date >= DATE '2025-11-01'
),
daily_debt AS (
  SELECT block_date, SUM(dart) AS dd FROM frobs GROUP BY block_date
),
cum_debt AS (
  SELECT block_date, SUM(dd) OVER (ORDER BY block_date) AS cum_debt FROM daily_debt
),

usds_flows AS (
  SELECT block_date,
    SUM(CASE WHEN "to"   = 0x8be042581f581E3620e29F213EA8b94afA1C8071 THEN amount ELSE 0 END)
    - SUM(CASE WHEN "from" = 0x8be042581f581E3620e29F213EA8b94afA1C8071 THEN amount ELSE 0 END) AS sub_net,
    SUM(CASE WHEN "to"   = 0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2 THEN amount ELSE 0 END)
    - SUM(CASE WHEN "from" = 0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2 THEN amount ELSE 0 END) AS alm_net
  FROM tokens.transfers
  WHERE blockchain = 'ethereum'
    AND contract_address = 0xdC035D45d973E3EC169d2276DDab16f1e407384F
    AND ("to" IN (0x8be042581f581E3620e29F213EA8b94afA1C8071, 0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2)
      OR "from" IN (0x8be042581f581E3620e29F213EA8b94afA1C8071, 0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2))
    AND block_date >= DATE '2025-11-01'
  GROUP BY block_date
),
cum_usds AS (
  SELECT block_date,
    SUM(sub_net) OVER (ORDER BY block_date) AS cum_sub_usds,
    SUM(alm_net) OVER (ORDER BY block_date) AS cum_alm_usds
  FROM usds_flows
),

susds_flows_alm AS (
  SELECT block_date,
    SUM(CASE WHEN "to" = 0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2 THEN amount ELSE 0 END)
    - SUM(CASE WHEN "from" = 0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2 THEN amount ELSE 0 END) AS net
  FROM tokens.transfers
  WHERE blockchain = 'ethereum'
    AND contract_address = 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD
    AND ("to" = 0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2 OR "from" = 0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2)
    AND block_date >= DATE '2025-11-01'
  GROUP BY block_date
),
cum_susds_alm AS (
  SELECT block_date, SUM(net) OVER (ORDER BY block_date) AS cum_susds FROM susds_flows_alm
),

venue_flows AS (
  SELECT block_date,
    SUM(CASE WHEN "to" = 0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2 THEN amount ELSE 0 END)
    - SUM(CASE WHEN "from" = 0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2 THEN amount ELSE 0 END) AS net
  FROM tokens.transfers
  WHERE blockchain = 'ethereum'
    AND contract_address = 0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b
    AND ("to" = 0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2 OR "from" = 0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2)
    AND block_date >= DATE '2025-11-01'
  GROUP BY block_date
),
cum_venue AS (
  SELECT block_date, SUM(net) OVER (ORDER BY block_date) AS cum_venue FROM venue_flows
),

usdc_deposits AS (
  SELECT block_date, SUM(amount) AS dep
  FROM tokens.transfers
  WHERE blockchain = 'ethereum'
    AND contract_address = 0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48
    AND "from" = 0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2
    AND "to"   = 0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b
    AND block_date >= DATE '2025-11-01'
  GROUP BY block_date
),
cum_cost AS (
  SELECT block_date, SUM(dep) OVER (ORDER BY block_date) AS cum_usdc FROM usdc_deposits
),

calendar AS (
  SELECT d AS block_date
  FROM UNNEST(SEQUENCE(DATE '2025-11-17', CURRENT_DATE, INTERVAL '1' DAY)) AS t(d)
),
prices AS (
  SELECT CAST(timestamp AS DATE) AS block_date, price
  FROM prices.day
  WHERE blockchain = 'ethereum'
    AND contract_address = 0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b
    AND timestamp >= TIMESTAMP '2025-11-01'
),

joined_pnl AS (
  SELECT c.block_date,
    cd.cum_debt, cu.cum_sub_usds, cu.cum_alm_usds, cs.cum_susds,
    v.cum_venue, cc.cum_usdc, p.price
  FROM calendar c
  LEFT JOIN cum_debt cd      ON cd.block_date = c.block_date
  LEFT JOIN cum_usds cu      ON cu.block_date = c.block_date
  LEFT JOIN cum_susds_alm cs ON cs.block_date = c.block_date
  LEFT JOIN cum_venue v      ON v.block_date  = c.block_date
  LEFT JOIN cum_cost cc      ON cc.block_date = c.block_date
  LEFT JOIN prices p         ON p.block_date  = c.block_date
),

filled_pnl AS (
  SELECT block_date,
    MAX(cum_debt)      OVER (ORDER BY block_date) AS cum_debt,
    MAX(cum_sub_usds)  OVER (ORDER BY block_date) AS cum_sub_usds,
    MAX(cum_alm_usds)  OVER (ORDER BY block_date) AS cum_alm_usds,
    MAX(cum_susds)     OVER (ORDER BY block_date) AS cum_susds,
    MAX(cum_venue)     OVER (ORDER BY block_date) AS cum_venue,
    MAX(cum_usdc)      OVER (ORDER BY block_date) AS cum_usdc,
    MAX(price)         OVER (ORDER BY block_date) AS price
  FROM joined_pnl
),

daily_pnl AS (
  SELECT
    block_date,
    COALESCE(cum_debt,0) - COALESCE(cum_sub_usds,0)
      - COALESCE(cum_alm_usds,0) - COALESCE(cum_susds,0)      AS agent_demand,
    (COALESCE(cum_debt,0) - COALESCE(cum_sub_usds,0)
      - COALESCE(cum_alm_usds,0) - COALESCE(cum_susds,0))
      * (POWER(CASE
          WHEN block_date < DATE '2025-11-07' THEN 1.048
          WHEN block_date < DATE '2025-11-11' THEN 1.0455
          WHEN block_date < DATE '2025-12-02' THEN 1.048
          WHEN block_date < DATE '2025-12-16' THEN 1.0455
          WHEN block_date < DATE '2026-03-09' THEN 1.043
          ELSE 1.0405
        END, 1.0/365) - 1)                                     AS sky_revenue,
    COALESCE(cum_venue,0) * COALESCE(price,0)                   AS pos_value,
    COALESCE(cum_usdc,0)                                        AS cost_basis,
    COALESCE(cum_venue,0) * COALESCE(price,0)
      - COALESCE(cum_usdc,0)                                    AS unrealized_gain
  FROM filled_pnl
  WHERE COALESCE(cum_debt,0) > 0
),

monthly_pnl_raw AS (
  SELECT
    DATE_TRUNC('month', block_date) AS month,
    LAST_VALUE(unrealized_gain) OVER (
      PARTITION BY DATE_TRUNC('month', block_date)
      ORDER BY block_date
      ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
    ) AS ug_end,
    FIRST_VALUE(unrealized_gain) OVER (
      PARTITION BY DATE_TRUNC('month', block_date)
      ORDER BY block_date
      ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
    ) AS ug_start,
    sky_revenue, agent_demand, block_date
  FROM daily_pnl
),
monthly_pnl AS (
  SELECT
    month,
    COUNT(*)                                AS days_pnl,
    MAX(agent_demand)                       AS agent_demand,
    MAX(ug_end) - MAX(ug_start)             AS prime_agent_revenue,
    SUM(sky_revenue)                        AS sky_revenue
  FROM monthly_pnl_raw
  GROUP BY month
),

-- ==========================================================================
-- B) Monthly Agent Rate section (mirrors shared query 6957966)
-- ==========================================================================
sub_usds_flows AS (
  SELECT block_date,
    SUM(CASE WHEN "to" = 0x8be042581f581E3620e29F213EA8b94afA1C8071 THEN amount ELSE 0 END)
    - SUM(CASE WHEN "from" = 0x8be042581f581E3620e29F213EA8b94afA1C8071 THEN amount ELSE 0 END) AS daily_net
  FROM tokens.transfers
  WHERE blockchain = 'ethereum'
    AND contract_address = 0xdC035D45d973E3EC169d2276DDab16f1e407384F
    AND ("to" = 0x8be042581f581E3620e29F213EA8b94afA1C8071 OR "from" = 0x8be042581f581E3620e29F213EA8b94afA1C8071)
    AND block_date >= DATE '2025-11-01'
  GROUP BY block_date
),
cum_sub_usds_only AS (
  SELECT block_date, SUM(daily_net) OVER (ORDER BY block_date) AS cum_usds FROM sub_usds_flows
),

sub_susds_flows AS (
  SELECT block_date,
    SUM(CASE WHEN "to" = 0x8be042581f581E3620e29F213EA8b94afA1C8071 THEN amount ELSE 0 END)
    - SUM(CASE WHEN "from" = 0x8be042581f581E3620e29F213EA8b94afA1C8071 THEN amount ELSE 0 END) AS daily_net
  FROM tokens.transfers
  WHERE blockchain = 'ethereum'
    AND contract_address = 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD
    AND ("to" = 0x8be042581f581E3620e29F213EA8b94afA1C8071 OR "from" = 0x8be042581f581E3620e29F213EA8b94afA1C8071)
    AND block_date >= DATE '2025-11-01'
  GROUP BY block_date
),
cum_sub_susds_only AS (
  SELECT block_date, SUM(daily_net) OVER (ORDER BY block_date) AS cum_susds FROM sub_susds_flows
),

joined_rate AS (
  SELECT c.block_date, u.cum_usds, s.cum_susds
  FROM calendar c
  LEFT JOIN cum_sub_usds_only  u ON u.block_date = c.block_date
  LEFT JOIN cum_sub_susds_only s ON s.block_date = c.block_date
),
filled_rate AS (
  SELECT block_date,
    MAX(cum_usds)  OVER (ORDER BY block_date) AS cum_usds,
    MAX(cum_susds) OVER (ORDER BY block_date) AS cum_susds
  FROM joined_rate
),
daily_rate AS (
  SELECT
    block_date,
    COALESCE(cum_usds, 0)
      * (POWER(CASE
          WHEN block_date < DATE '2025-11-07' THEN 1.047
          WHEN block_date < DATE '2025-11-11' THEN 1.0445
          WHEN block_date < DATE '2025-12-02' THEN 1.047
          WHEN block_date < DATE '2025-12-16' THEN 1.0445
          WHEN block_date < DATE '2026-03-09' THEN 1.042
          ELSE 1.0395
        END, 1.0/365) - 1)                    AS agent_rate_usds,
    COALESCE(cum_susds, 0)
      * (POWER(1.002, 1.0/365) - 1)           AS agent_rate_susds
  FROM filled_rate
  WHERE COALESCE(cum_usds, 0) > 0 OR COALESCE(cum_susds, 0) > 0
),
monthly_rate AS (
  SELECT
    DATE_TRUNC('month', block_date)       AS month,
    COUNT(*)                              AS days_rate,
    SUM(agent_rate_usds + agent_rate_susds) AS agent_rate
  FROM daily_rate
  GROUP BY DATE_TRUNC('month', block_date)
)

-- ==========================================================================
-- C) Final join — one row per month
-- ==========================================================================
SELECT
  COALESCE(p.month, r.month)                                  AS month,
  p.days_pnl,
  r.days_rate,
  p.agent_demand,
  p.prime_agent_revenue,
  r.agent_rate,
  COALESCE(p.prime_agent_revenue, 0) + COALESCE(r.agent_rate, 0)  AS obex_revenue_total,
  p.sky_revenue,
  COALESCE(p.prime_agent_revenue, 0) + COALESCE(r.agent_rate, 0)
    - COALESCE(p.sky_revenue, 0)                              AS monthly_pnl,
  SUM(COALESCE(p.prime_agent_revenue, 0) + COALESCE(r.agent_rate, 0)
        - COALESCE(p.sky_revenue, 0))
    OVER (ORDER BY COALESCE(p.month, r.month))                AS cumulative_pnl
FROM monthly_pnl p
FULL OUTER JOIN monthly_rate r ON p.month = r.month
ORDER BY month
