-- Last block_number on each calendar day (UTC), in a date range.
--
-- Used by ``DuneBlockResolver`` to bulk-load (date → max_block) mappings for
-- a prime's lifetime in one shot, replacing per-day RPC binary searches.
--
-- Parameters:
--   {{start_date}}   text       — 'YYYY-MM-DD', inclusive
--   {{end_date}}     text       — 'YYYY-MM-DD', inclusive
--   {{pin_block}}    number     — upper-bound cutoff (also part of cache key)
--
-- Output columns: block_date, block_number

SELECT
  CAST(time AS DATE) AS block_date,
  MAX(number)        AS block_number
FROM ethereum.blocks
WHERE time     >= DATE '{{start_date}}'
  AND time     <  DATE '{{end_date}}' + INTERVAL '1' DAY
  AND number   <= {{pin_block}}
GROUP BY CAST(time AS DATE)
ORDER BY block_date
