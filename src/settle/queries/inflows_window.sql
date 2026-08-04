-- USDC inflows to a holder within a block window (who funded them, and when).
-- Ad-hoc probe for the Grove subproxy investigation.
SELECT
    "from"            AS from_addr,
    value,
    evt_block_number,
    evt_block_time,
    evt_tx_hash
FROM erc20_ethereum.evt_Transfer
WHERE "to" = {{holder}}
  AND contract_address = {{token}}
  AND evt_block_number BETWEEN {{from_block}} AND {{to_block}}
  AND evt_block_number >= {{pin_block}}
ORDER BY evt_block_time
