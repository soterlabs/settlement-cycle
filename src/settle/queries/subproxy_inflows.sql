-- All USDC + AUSD inflows to a given holder on Ethereum, full history.
-- Ad-hoc probe (Grove subproxy investigation). pin_block is a no-op floor
-- (set 0) included only because the runtime always binds it.
SELECT
    contract_address,
    "from"            AS from_addr,
    "to"              AS to_addr,
    value,
    evt_block_number,
    evt_block_time,
    evt_tx_hash
FROM erc20_ethereum.evt_Transfer
WHERE "to" = {{holder}}
  AND contract_address IN ({{usdc}}, {{ausd}})
  AND evt_block_number >= {{pin_block}}
ORDER BY evt_block_time
