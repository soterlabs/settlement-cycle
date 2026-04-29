-- Per-day, per-counterparty token flow into/out of a single holder.
--
-- Used by Cat A source-tagged inflow accounting: the Python helper classifies
-- each row's counterparty against the prime's `external_alm_sources` allowlist.
-- Counterparties IN the allowlist are off-chain custodian senders (Anchorage-
-- style realized yield) and pass through to revenue. Every other counterparty
-- (PSM swap leg, venue contract, allocator buffer, mint/burn) is treated as
-- value-preserving capital and netted out of revenue.
--
-- Reads `tokens.transfers.amount`, which is DECIMAL-ADJUSTED (human-readable
-- units) per the Dune spellbook convention. Do NOT swap this for
-- `erc20.evt_Transfer.value` (raw uint256) without rescaling by 10^decimals.
--
-- Parameters:
--   {{chain}}        text       — e.g. 'ethereum', 'base'
--   {{token}}        varbinary  — token contract address
--   {{holder}}       varbinary  — the ALM proxy address
--   {{start_date}}   text       — 'YYYY-MM-DD'
--   {{pin_block}}    number     — upper-bound block_number cutoff
--
-- Output columns: block_date, counterparty, signed_amount
--   counterparty: the OTHER side of the transfer (= "from" if to=holder, = "to" if from=holder)
--   signed_amount: positive on inflow, negative on outflow (decimal-adjusted)

WITH directed AS (
  SELECT
    block_date,
    CASE WHEN "to" = {{holder}} THEN "from" ELSE "to" END AS counterparty,
    CASE WHEN "to" = {{holder}} THEN amount ELSE -amount END AS signed_amount
  FROM tokens.transfers
  WHERE blockchain        = '{{chain}}'
    AND contract_address = {{token}}
    AND ("to" = {{holder}} OR "from" = {{holder}})
    AND block_date      >= DATE '{{start_date}}'
    AND block_number    <= {{pin_block}}
)
SELECT
  block_date,
  counterparty,
  SUM(signed_amount) AS signed_amount
FROM directed
GROUP BY block_date, counterparty
ORDER BY block_date, counterparty
