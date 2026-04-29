-- Daily cumulative ilk debt from frob calls.
--
-- Parameters:
--   {{ilk_bytes32}}  varbinary  — 32-byte ilk identifier
--   {{start_date}}   text       — 'YYYY-MM-DD'; lower bound for block_date partition pruning
--   {{pin_block}}    number     — upper bound block_number cutoff
--
-- Output columns: block_date, daily_dart, cum_debt
--
-- Detection: traces to the Vat (0x35D1...492B) where input begins with the frob
-- selector (0x76088703) and the second 32 bytes equal the ilk. The dart parameter
-- (signed debt delta) sits at offset 165 = 4 (selector) + 32×5 (i, u, v, dink).
-- Cumulative dart is in 1e18 units (USDS).

-- DECIMAL(38,18) preserves int256/1e18 exactly up to ~9.2e18 USDS — DOUBLE
-- (53-bit mantissa) loses precision at the ULP-of-1e26 level (~$11K per frob
-- at 100M USDS positions), and the loss propagates through `_to_decimal(str(v))`
-- in the Python source. Using DECIMAL keeps every dart byte-exact end to end.
WITH frobs AS (
  SELECT
    tr.block_date,
    CAST(bytearray_to_int256(substr(tr.input, 165, 32)) AS DECIMAL(38, 0))
      / CAST(1000000000000000000 AS DECIMAL(38, 0)) AS dart
  FROM ethereum.traces tr
  WHERE tr."to"          = 0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B
    AND substr(tr.input, 1, 4)  = 0x76088703
    AND substr(tr.input, 5, 32) = {{ilk_bytes32}}
    AND tr.success         = true
    AND tr.block_date    >= DATE '{{start_date}}'
    AND tr.block_number <= {{pin_block}}
),
daily AS (
  SELECT block_date, SUM(dart) AS daily_dart
  FROM frobs
  GROUP BY block_date
)
SELECT
  block_date,
  daily_dart,
  SUM(daily_dart) OVER (ORDER BY block_date) AS cum_debt
FROM daily
ORDER BY block_date
