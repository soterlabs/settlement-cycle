-- Highest block on `chain` whose timestamp ≤ `ts`. Deterministic given a
-- fixed historical timestamp (e.g. a month boundary in UTC), so the @cached
-- wrapper around ``find_block_at_or_before`` keeps the result for the life
-- of the cache.
--
-- Replaces the per-anchor binary search in ``rpc.find_block_at_or_before``
-- (~25 RPC calls + 1 non-deterministic ``latest_block`` call).
--
-- Parameters:
--   {{chain}}  text       — Dune blockchain name ('ethereum', 'base',
--                           'arbitrum', 'optimism', 'avalanche_c'). Must be
--                           a chain that has a populated ``evms.blocks`` row.
--   {{ts}}     datetime   — ISO-8601 UTC timestamp; the answer is the latest
--                           block whose ``evms.blocks.time`` is ≤ ts.
--
-- Output: one row, one column ``block_number``.

SELECT MAX(number) AS block_number
FROM evms.blocks
WHERE blockchain = '{{chain}}'
  AND time      <= TIMESTAMP '{{ts}}'
