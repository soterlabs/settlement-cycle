-- Per-event Transfer log for an aToken/holder pair, capturing precise
-- block numbers (not daily aggregates).
--
-- Used by the Cat C per-segment yield path (``_atoken_per_segment_yield``)
-- to derive sub-day-resolution event boundaries. Day-resolution
-- boundaries (the existing ``atoken_{vid}_mints``/``burns`` daily
-- aggregates converted via ``block_at_or_before(EOD d)``) bucket all
-- events on day ``d`` to a single boundary block at the end of that
-- day. When two events fall on the same day or on consecutive days,
-- the per-segment closed-form mishandles the intraday/inter-event
-- yield because the formula attributes rebase on the segment-START
-- scaled basis only — yield earned on a newly-minted scaled balance
-- between the mint time and the next boundary is lost. With per-event
-- block resolution each event gets its own boundary, eliminating that
-- precision loss (down to one ``balanceOf`` read per event).
--
-- One row per Transfer event involving the holder, ordered by block.
-- Mints (from = zero), burns (to = zero), and any third-party transfer
-- in/out of the holder all appear here — the Python helper just needs
-- the block numbers.
--
-- Parameters (typed by ``_infer_parameters`` based on name):
--   {{chain}}        text       — Dune blockchain name
--   {{token}}        varbinary  — aToken contract address
--   {{holder}}       varbinary  — ALM proxy address
--   {{start_date}}   text       — 'YYYY-MM-DD' (prime-start cutoff)
--   {{pin_block}}    number     — upper-bound block_number cutoff
--
-- Output columns: block_number, evt_index, signed_amount, counterparty

SELECT
  block_number,
  evt_index,
  -- Positive when the holder receives, negative when it sends.
  CASE WHEN "to" = {{holder}} THEN amount      ELSE -amount END AS signed_amount,
  -- The other party on the transfer.
  CASE WHEN "to" = {{holder}} THEN "from"      ELSE "to"     END AS counterparty
FROM tokens.transfers
WHERE blockchain        = '{{chain}}'
  AND contract_address = {{token}}
  AND ("from" = {{holder}} OR "to" = {{holder}})
  AND block_date      >= DATE '{{start_date}}'
  AND block_number    <= {{pin_block}}
ORDER BY block_number, evt_index
