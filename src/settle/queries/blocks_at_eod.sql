-- Last block_number on each calendar day (UTC), in a date range.
--
-- Used by ``DuneBlockResolver`` to bulk-load (date → max_block) mappings for
-- a prime's lifetime in one shot, replacing per-day RPC binary searches.
--
-- Uses ``evms.blocks`` (the unified cross-chain Dune spellbook table) so that
-- ``chain`` is a standard text value parameter rather than a table-name
-- substitution — Dune does not support {{param}} in FROM identifiers reliably.
--
-- Parameters:
--   {{chain}}        text       — Dune blockchain name (e.g. 'ethereum', 'base',
--                                 'arbitrum', 'optimism', 'avalanche_c').
--                                 Matched against evms.blocks.blockchain column.
--   {{start_date}}   text       — 'YYYY-MM-DD', inclusive
--   {{end_date}}     text       — 'YYYY-MM-DD', inclusive
--
-- Note: pin_block is used as a cache-key discriminator in the Python layer but
-- is intentionally NOT passed to Dune as a query parameter — Dune rejects
-- parameters not declared in the saved query's schema (HTTP 400). The date
-- filter already bounds results to the settlement period.
--
-- Output columns: block_date, block_number

SELECT
  CAST(time AS DATE) AS block_date,
  MAX(number)        AS block_number
FROM evms.blocks
WHERE blockchain = '{{chain}}'
  AND time     >= DATE '{{start_date}}'
  AND time     <  DATE '{{end_date}}' + INTERVAL '1' DAY
GROUP BY CAST(time AS DATE)
ORDER BY block_date
