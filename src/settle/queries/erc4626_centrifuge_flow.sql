-- ERC-4626 Deposit / Withdraw event-based net flow for a Centrifuge vault.
--
-- Used by Cat E (RWA_TRANCHE) venues whose ALM interacts with the vault
-- contract directly via ERC-4626 deposit / withdraw rather than secondary-
-- market token transfers.  Captures the exact underlying-asset (e.g. USDC)
-- amounts in/out, matching external-party cash-flow accounting.
--
-- Deposit  (address indexed sender, address indexed owner, uint256 assets, uint256 shares)
--   topic0 = 0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7
--   topic1 = sender (Grove ALM — the account calling deposit())
--
-- Withdraw (address indexed sender, address indexed receiver, address indexed owner, uint256 assets, uint256 shares)
--   topic0 = 0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc9667232c8db
--   topic2 = receiver (Grove ALM — receives the redeemed underlying)
--
-- Both events encode:  data = assets(uint256, 32 bytes) ++ shares(uint256, 32 bytes)
-- Assets are in underlying-token units (caller divides by 10^underlying_decimals).
-- Shares are in vault-token units (caller divides by 10^share_decimals).
--
-- Parameters:
--   {{vault}}       varbinary  — ERC-4626 vault contract address (20 bytes)
--   {{holder}}      varbinary  — ALM address (20 bytes)
--   {{start_date}}  text       — 'YYYY-MM-DD'  lower-bound on block_date
--   {{pin_block}}   number     — upper-bound block_number (settlement cutoff)
--
-- Output columns (raw integers — no decimal normalisation):
--   block_date        date
--   assets_in_raw     uint256  — sum of deposit assets on this day
--   assets_out_raw    uint256  — sum of withdraw assets on this day
--   shares_in_raw     uint256  — sum of deposit shares on this day
--   shares_out_raw    uint256  — sum of withdraw shares on this day

WITH
all_events AS (
  -- Deposit: topic0, topic1=sender, data = assets ++ shares
  SELECT
    block_date,
    'deposit'                                      AS kind,
    bytearray_to_uint256(substr(data, 1, 32))      AS assets_raw,
    bytearray_to_uint256(substr(data, 33, 32))     AS shares_raw
  FROM ethereum.logs
  WHERE contract_address = {{vault}}
    AND block_number     <= {{pin_block}}
    AND block_date       >= DATE '{{start_date}}'
    AND topic0           = 0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7
    AND topic1           = concat(0x000000000000000000000000, {{holder}})

  UNION ALL

  -- Withdraw: topic0, topic1=sender, topic2=receiver, data = assets ++ shares
  SELECT
    block_date,
    'withdraw'                                     AS kind,
    bytearray_to_uint256(substr(data, 1, 32))      AS assets_raw,
    bytearray_to_uint256(substr(data, 33, 32))     AS shares_raw
  FROM ethereum.logs
  WHERE contract_address = {{vault}}
    AND block_number     <= {{pin_block}}
    AND block_date       >= DATE '{{start_date}}'
    AND topic0           = 0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc9667232c8db
    AND topic2           = concat(0x000000000000000000000000, {{holder}})
)

SELECT
  block_date,
  SUM(CASE WHEN kind = 'deposit'  THEN assets_raw ELSE uint256 '0' END) AS assets_in_raw,
  SUM(CASE WHEN kind = 'withdraw' THEN assets_raw ELSE uint256 '0' END) AS assets_out_raw,
  SUM(CASE WHEN kind = 'deposit'  THEN shares_raw ELSE uint256 '0' END) AS shares_in_raw,
  SUM(CASE WHEN kind = 'withdraw' THEN shares_raw ELSE uint256 '0' END) AS shares_out_raw
FROM all_events
GROUP BY block_date
ORDER BY block_date
