-- Independent Dune trace oracle: preserve signed integer wad through SUM.
-- Same trace filters as debt_timeseries.sql; normalize only in Python.
-- VARCHAR prevents the API transport from rounding large decimal values.
WITH events AS (
  SELECT tr.block_date,
         CAST(bytearray_to_int256(substr(tr.input, 165, 32)) AS DECIMAL(38, 0)) AS dart_raw
  FROM ethereum.traces tr
  WHERE tr."to" = 0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B
    AND substr(tr.input, 1, 4) IN (0x76088703, 0x7bab3f40)
    AND substr(tr.input, 5, 32) = {{ilk_bytes32}}
    AND tr.success = true
    AND tr.block_date >= DATE '{{start_date}}'
    AND tr.block_number <= {{pin_block}}
), daily AS (
  SELECT block_date, SUM(dart_raw) AS daily_dart_raw
  FROM events
  GROUP BY block_date
)
SELECT block_date,
       CAST(daily_dart_raw AS VARCHAR) AS daily_dart_raw,
       CAST(SUM(daily_dart_raw) OVER (ORDER BY block_date) AS VARCHAR) AS cum_debt_raw
FROM daily
ORDER BY block_date
