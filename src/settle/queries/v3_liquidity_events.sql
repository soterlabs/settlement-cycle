-- IncreaseLiquidity / DecreaseLiquidity events emitted by a Uniswap V3
-- NonfungiblePositionManager, scoped to a list of tokenIds (= positions held
-- by the prime's ALM in the target pool).
--
-- Parameters:
--   {{nfpm}}              text       — NFPM contract address (canonical
--                                      0xC36442b4a4522E871399CD717aBDD847Ab11FE88
--                                      on every V3 EVM chain)
--   {{token_ids_padded}}  text       — comma-separated 32-byte-padded tokenIds
--                                      (e.g. ``0x000…123e7f,0x000…456abc``).
--                                      Constructed by the source after
--                                      enumerating positions via RPC at the
--                                      period boundaries.
--   {{from_block}}        number     — exclusive lower bound (events at
--                                      block_number > from_block)
--   {{pin_block}}         number     — inclusive upper bound (block_number ≤
--                                      pin_block); also part of the cache key
--                                      and naming convention for all our SQL.
--
-- Output columns: block_number, block_time, tx_hash, log_index, topic0,
-- topic1 (= tokenId padded), data (raw 96-byte hex: liquidity, amount0, amount1).
-- The Python source decodes ``data`` via ``_decode_liquidity_log``.

SELECT
  block_number,
  block_time,
  tx_hash,
  index AS log_index,
  topic0,
  topic1,
  data
FROM ethereum.logs
WHERE contract_address = {{nfpm}}
  AND topic0 IN (
    0x3067048beee31b25b2f1681f88dac838c8bba36af25bfb2b7cf7473a5847e35f,  -- IncreaseLiquidity
    0x26f6a048ee9138f2c0ce266f322cb99228e8d619ae2bff30c67f8dcf9d2377b4   -- DecreaseLiquidity
  )
  AND topic1 IN ({{token_ids_padded}})
  AND block_number  >  {{from_block}}
  AND block_number  <= {{pin_block}}
ORDER BY block_number, log_index
