-- SSR rate boundaries from SP-BEAM `file()` calls on sUSDS.
--
-- Parameters:
--   {{start_date}}   text       — 'YYYY-MM-DD' lower bound (typically the prime's calendar_start_date)
--   {{pin_block}}    number     — upper-bound block_number cutoff
--
-- Output columns: effective_date, ssr_apy
--
-- Reads `file(bytes32("ssr"), uint256)` traces on sUSDS (0xa3931d71...fbD).
-- The `ssr` parameter is `bytes32("ssr")` = 0x7373720000000000000000000000000000000000000000000000000000000000.
-- The new rate sits at offset 37 = 4 (selector) + 32 (key) + 1; raw value is RAY-scaled
-- per-second rate, converted to APY.

WITH file_calls AS (
  SELECT
    tr.block_time,
    tr.block_date,
    CAST(bytearray_to_uint256(substr(tr.input, 37, 32)) AS DOUBLE) AS rate_per_second_ray
  FROM ethereum.traces tr
  WHERE tr."to"          = 0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD
    AND substr(tr.input, 1, 4) = 0x29ae8114  -- file(bytes32, uint256)
    AND substr(tr.input, 5, 32) = 0x7373720000000000000000000000000000000000000000000000000000000000
    AND tr.success       = true
    AND tr.block_date  >= DATE '{{start_date}}'
    AND tr.block_number <= {{pin_block}}
),
-- Multiple file() calls can land on the same UTC day (rate change followed
-- by an immediate correction, etc.). Keep only the chronologically last call
-- per day — that's the rate in effect at end-of-day, which is what the
-- downstream `ssr_at_or_before` resolver assumes.
deduped AS (
  SELECT
    block_date,
    rate_per_second_ray,
    ROW_NUMBER() OVER (PARTITION BY block_date ORDER BY block_time DESC) AS rn
  FROM file_calls
)
SELECT
  block_date AS effective_date,
  POWER(rate_per_second_ray / 1e27, 31536000) - 1 AS ssr_apy
FROM deduped
WHERE rn = 1
ORDER BY block_date
