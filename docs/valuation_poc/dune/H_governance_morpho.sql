-- Category H — Governance (MORPHO @ Spark Base ALM)
-- Cutoff: Base block 45096799 (2026-04-23 22:22:25Z)
-- Expected (Python eth_call + CoinGecko): 0.806652 MORPHO × $1.92 ≈ $1.55
-- Dune query id: 7365846

WITH balance AS (
  SELECT
    SUM(CASE WHEN "to"   = 0x2917956eff0b5eaf030abdb4ef4296df775009ca THEN amount ELSE 0 END) -
    SUM(CASE WHEN "from" = 0x2917956eff0b5eaf030abdb4ef4296df775009ca THEN amount ELSE 0 END) AS balance_units
  FROM tokens.transfers
  WHERE blockchain = 'base'
    AND contract_address = 0xbaa5cc21fd487b8fcc2f632f3f4e8d37262a0842
    AND ("to"   = 0x2917956eff0b5eaf030abdb4ef4296df775009ca
      OR "from" = 0x2917956eff0b5eaf030abdb4ef4296df775009ca)
    AND block_number <= 45096799
),
-- prices.minute is huge; narrow to a same-day window to fit memory.
latest_price AS (
  SELECT price
  FROM prices.minute
  WHERE blockchain = 'base'
    AND contract_address = 0xbaa5cc21fd487b8fcc2f632f3f4e8d37262a0842
    AND timestamp >= TIMESTAMP '2026-04-23 12:00:00 UTC'
    AND timestamp <= TIMESTAMP '2026-04-23 22:22:25 UTC'
  ORDER BY timestamp DESC
  LIMIT 1
)
SELECT
  b.balance_units                              AS balance_morpho,
  p.price                                      AS unit_price_usd,
  b.balance_units * p.price                    AS value_usd
FROM balance b, latest_price p;
