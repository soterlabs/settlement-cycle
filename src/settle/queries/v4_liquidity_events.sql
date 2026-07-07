-- ModifyLiquidity events emitted by the Uniswap V4 PoolManager, scoped to a
-- single pool (topic1 = poolId) and a single sender (topic2 = the v4
-- PositionManager — the position ``owner`` for NFT positions; scoping here
-- prevents salt collisions from other LPs, see
-- ``extract.uniswap_v4.read_modify_liquidity_events``).
--
-- Replaces the paginated RPC ``eth_getLogs`` scan for settlement runs:
-- free-tier RPC providers cap/time-out on month-long log ranges (Alchemy:
-- 10-block cap; drpc: per-request compute timeout), while one cached Dune
-- execution covers the whole period.
--
-- Parameters:
--   {{chain}}          text    — Dune blockchain name (matched against
--                                evms.logs.blockchain; table names can't be
--                                parameterized, evms.* is the multichain way)
--   {{pool_manager}}   text    — v4 PoolManager address (0x…)
--   {{topic0}}         text    — ModifyLiquidity event topic (computed from
--                                the ABI signature in extract.uniswap_v4,
--                                passed in to keep one source of truth)
--   {{pool_id}}        text    — 32-byte poolId (0x…)
--   {{sender_padded}}  text    — 32-byte-padded sender address (0x…)
--   {{from_block}}     number  — exclusive lower bound
--   {{pin_block}}      number  — inclusive upper bound; also part of the
--                                cache key per our SQL conventions
--
-- Output columns: block_number, block_time, tx_hash, log_index, data
-- (raw 128-byte hex: tickLower, tickUpper, liquidityDelta, salt). The
-- Python source decodes ``data`` via ``decode_modify_liquidity_log``.

SELECT
  block_number,
  block_time,
  tx_hash,
  index AS log_index,
  data
FROM evms.logs
WHERE blockchain = '{{chain}}'
  AND contract_address = {{pool_manager}}
  AND topic0 = {{topic0}}
  AND topic1 = {{pool_id}}
  AND topic2 = {{sender_padded}}
  AND block_number  >  {{from_block}}
  AND block_number  <= {{pin_block}}
ORDER BY block_number, index
