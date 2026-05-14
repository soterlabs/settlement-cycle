-- Sum of decimal-adjusted token transfers FROM `sender` INTO `holder`
-- across a closed date window, capped at `pin_block`.
--
-- Used by the Cat C aToken external-rewards path: Merkl-style yield
-- distributors drop aTokens directly into the prime's ALM, and the closed-
-- form ``balanceOf × index`` revenue formula in ``_atoken_index_weighted_inflow``
-- buckets these as principal injection rather than yield. This query lets
-- the compute layer add the aToken inflow back as a separate
-- ``external_revenue`` stream (USD = sum_amount × $1 for par-stable
-- underlying — the only supported case today; the Python helper rejects
-- non-par underlyings rather than silently mispricing).
--
-- One Dune query per ``(chain, token, sender)`` triple. With Grove's
-- current config (one external_alm_sources entry on Ethereum: the Merkl
-- distributor) that's three Dune calls per Cat C cell (one per E1/E2/E3),
-- all small (single aggregate row).
--
-- Parameters: typed by the name-based inference in ``_infer_parameters``.
-- Address-shaped params land as ``text`` on the Dune side (the Python
-- ``_format_param`` helper renders ``bytes`` as ``0x``-prefixed hex); Dune
-- casts back to varbinary for comparison against the table's varbinary
-- columns. The semantic VALUE is varbinary; the Dune param TYPE is text.
--
--   {{chain}}        text   — Dune blockchain name ('ethereum', 'base', …)
--   {{token}}        text   — aToken contract address as 0x-hex (the venue
--                             token, not the underlying)
--   {{holder}}       text   — ALM proxy address as 0x-hex
--   {{sender}}       text   — single allowlisted external source address as
--                             0x-hex; the caller loops over each entry in
--                             ``prime.external_alm_sources[chain]``
--   {{start_date}}   text   — period start as 'YYYY-MM-DD' (inclusive)
--   {{end_date}}     text   — period end as 'YYYY-MM-DD' (inclusive)
--   {{pin_block}}    number — upper-bound block_number (also part of the
--                             @cached key on the Python side)
--
-- Output: single row, single column ``total_amount`` (decimal-adjusted; 0
-- if no qualifying transfers — never NULL).

SELECT COALESCE(SUM(amount), 0) AS total_amount
FROM tokens.transfers
WHERE blockchain      = '{{chain}}'
  AND contract_address = {{token}}
  AND "to"             = {{holder}}
  AND "from"           = {{sender}}
  AND block_date      >= DATE '{{start_date}}'
  AND block_date      <= DATE '{{end_date}}'
  AND block_number    <= {{pin_block}}
