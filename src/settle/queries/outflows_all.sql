-- All USDC outflows FROM a holder, full history (where did swept funds go).
-- Ad-hoc probe for the Grove subproxy investigation.
SELECT
    "to"              AS to_addr,
    value,
    evt_block_number,
    evt_block_time,
    evt_tx_hash
FROM erc20_ethereum.evt_Transfer
WHERE "from" = {{holder}}
  AND contract_address = {{token}}
  AND evt_block_number >= {{pin_block}}
ORDER BY evt_block_time
