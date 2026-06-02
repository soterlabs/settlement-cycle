-- Daily cumulative ilk debt from frob + grab calls.
--
-- Parameters:
--   {{ilk_bytes32}}  varbinary  — 32-byte ilk identifier
--   {{start_date}}   text       — 'YYYY-MM-DD'; lower bound for block_date partition pruning
--   {{pin_block}}    number     — upper bound block_number cutoff
--
-- Output columns: block_date, daily_dart, cum_debt
--
-- Detection: traces to the Vat (0x35D1...492B) with selector + ilk-match. Two
-- selectors contribute to ``urns[ilk][u].art``:
--
--   * ``frob(bytes32 i, address u, address v, address w, int dink, int dart)``
--     selector 0x76088703 — regular principal draws/repays. dart at offset 165.
--   * ``grab(bytes32 i, address u, address v, address w, int dink, int dart)``
--     selector 0x7bab3f40 — Maker's "grab and credit vow" path. For allocator-
--     style ilks (no traditional liquidations) this is repurposed as the
--     interest-capitalization mechanism: Sky governance periodically calls
--     ``vat.grab`` with positive ``dart`` to convert accrued stability fee
--     into the urn's principal (instead of using ``jug.drip``/``vat.fold``,
--     which would update the rate index — verified: rate stays at 1e27 for
--     ALLOCATOR-BLOOM-A). dart at offset 165 (same layout as frob).
--
-- Both event types add their signed ``dart`` to ``urns[ilk][u].art``, so
-- summing them gives the live on-chain ``Vat.ilks(ilk).Art`` (= BA Labs's
-- "liabilities" value). The frob-only version of this query under-counted
-- by ~$15M at Jan 2026 dates (=cumulative grab dart through Dec 2025) and
-- ~$48M by Apr 30. Including grabs aligns cum_debt with the canonical Vat
-- state.
--
-- ⚠ Methodology note: when grabs are included, the BR-on-utilised charge
-- compounds slightly (BR is applied to a base that already contains
-- previously-capitalised interest). The economic effect is the same as
-- daily-compounding stability fees — but it's a step away from Grove's
-- xlsx "Subscriptions" methodology, which is frob-only. Confirm with
-- the prime team before treating the grab-inclusive number as the
-- canonical "Subscriptions" base for the cost-of-funds split.
--
-- DECIMAL(38,18) preserves int256/1e18 exactly up to ~9.2e18 USDS — DOUBLE
-- (53-bit mantissa) loses precision at the ULP-of-1e26 level (~$11K per frob
-- at 100M USDS positions), and the loss propagates through `_to_decimal(str(v))`
-- in the Python source. Using DECIMAL keeps every dart byte-exact end to end.
WITH events AS (
  -- frob: regular debt draws/repays
  SELECT
    tr.block_date,
    CAST(bytearray_to_int256(substr(tr.input, 165, 32)) AS DECIMAL(38, 0))
      / CAST(1000000000000000000 AS DECIMAL(38, 0)) AS dart
  FROM ethereum.traces tr
  WHERE tr."to"          = 0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B
    AND substr(tr.input, 1, 4)  = 0x76088703
    AND substr(tr.input, 5, 32) = {{ilk_bytes32}}
    AND tr.success         = true
    AND tr.block_date    >= DATE '{{start_date}}'
    AND tr.block_number <= {{pin_block}}

  UNION ALL

  -- grab: stability-fee capitalisation (allocator-ilk usage; not liquidation)
  SELECT
    tr.block_date,
    CAST(bytearray_to_int256(substr(tr.input, 165, 32)) AS DECIMAL(38, 0))
      / CAST(1000000000000000000 AS DECIMAL(38, 0)) AS dart
  FROM ethereum.traces tr
  WHERE tr."to"          = 0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B
    AND substr(tr.input, 1, 4)  = 0x7bab3f40
    AND substr(tr.input, 5, 32) = {{ilk_bytes32}}
    AND tr.success         = true
    AND tr.block_date    >= DATE '{{start_date}}'
    AND tr.block_number <= {{pin_block}}
),
daily AS (
  SELECT block_date, SUM(dart) AS daily_dart
  FROM events
  GROUP BY block_date
)
SELECT
  block_date,
  daily_dart,
  SUM(daily_dart) OVER (ORDER BY block_date) AS cum_debt
FROM daily
ORDER BY block_date
