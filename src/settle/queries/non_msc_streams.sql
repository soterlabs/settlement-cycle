-- Non-MSC Sky protocol P&L — all streams for one calendar month, one execution.
--
-- Methodology: https://hackmd.io/@W57nO5PyRMKhcLqjvsLifw/S1zxTDpXMg
-- (validated May 2026: every line reproduced to the dollar — see PRD §17.13).
--
-- Parameters:
--   {{month_start}}      date  — first day of the reported month
--   {{month_end_excl}}   date  — first day of the NEXT month
--   {{burn_end_excl}}    date  — first day of month+2 (jar-burn attribution window end)
--   {{pin_block}}        number — upper block cutoff (must be ≥ the burn window)
--
-- Output rows: (stream, label, event_date, amount) —
--   income:psm_jar       one row per jar burn in (month_end, next_month_end]
--                        ("the first jar burn after a month ends is that month's
--                        income"; windowing by (m_end, m_end_next] additionally
--                        attributes any EXTRA burn in the window to the same
--                        month so no burn is ever dropped)
--   income:stability_fee one row per ilk (Σ over the month's vat.fold calls of
--                        Art(ilk, at that trace) × Δrate/1e27 — exactly what
--                        fold credits to the vow)
--   expense:susds_drip   sUSDS SSR recognized at drip (gross, all holders)
--   expense:susds_prime  SSR accrued to PRIME-held sUSDS (ALM + subproxy
--                        holders) — already netted inside MSC via BR − 30bps /
--                        agent rate; the report DEDUCTS this from the gross
--   expense:dsr_drip     legacy DSR (vat.suck minting to the pot)
--   expense:stusds_drip  stUSDS staking interest recognized at drip
--
-- Precision: drip/burn lines are exact DECIMAL sums ÷ 1e18. The fee and
-- prime-carve-out lines multiply 1e27-scale factors and use DOUBLE (relative
-- error ~1e-15, sub-cent on $M lines; validated to the dollar vs the target).

WITH fee_ilks AS (
  -- The 9 whitelisted core-vault ilks (config/non_msc.yaml documents this list;
  -- the SQL is the executable copy).
  SELECT * FROM (VALUES
    (0x4554482d41000000000000000000000000000000000000000000000000000000, 'ETH-A'),
    (0x4554482d42000000000000000000000000000000000000000000000000000000, 'ETH-B'),
    (0x4554482d43000000000000000000000000000000000000000000000000000000, 'ETH-C'),
    (0x5753544554482d41000000000000000000000000000000000000000000000000, 'WSTETH-A'),
    (0x5753544554482d42000000000000000000000000000000000000000000000000, 'WSTETH-B'),
    (0x574254432d410000000000000000000000000000000000000000000000000000, 'WBTC-A'),
    (0x574254432d420000000000000000000000000000000000000000000000000000, 'WBTC-B'),
    (0x574254432d430000000000000000000000000000000000000000000000000000, 'WBTC-C'),
    (0x4c534556322d534b592d41000000000000000000000000000000000000000000, 'LSEV2-SKY-A')
  ) AS t(ilk, label)
),
prime_holders AS (
  -- Prime sUSDS holders on Ethereum (ALM proxies + subproxies) whose SSR is
  -- MSC-accounted. Source of truth: config/<prime>.yaml alm/subproxy blocks;
  -- keep in sync (documented in config/non_msc.yaml).
  SELECT * FROM (VALUES
    (0x1601843c5e9bc251a3272907010afa41fa18347e, 'spark_alm'),
    (0x491edfb0b8b608044e227225c715981a30f3a44e, 'grove_alm'),
    (0xb6dd7ae22c9922afee0642f9ac13e58633f715a2, 'obex_alm'),
    (0x3300f198988e4c9c63f75df86de36421f06af8c4, 'spark_sub'),
    (0x1369f7b2b38c76b6478c0f0e66d94923421891ba, 'grove_sub'),
    (0x8be042581f581e3620e29f213ea8b94afa1c8071, 'obex_sub'),
    (0x355cd90ecb1b409fdf8b64c4473c3b858da2c310, 'keel_sub'),
    (0x08978e3700859e476201c1d7438b3427e3c81140, 'skybase_sub')
  ) AS t(addr, label)
),

-- ── income: PSM / Coinbase jar burns ───────────────────────────────────────
jar_burns AS (
  SELECT evt_block_date AS d,
         CAST(CAST(value AS DECIMAL(38,0)) AS DOUBLE) / 1e18 AS amount
  FROM erc20_ethereum.evt_transfer
  WHERE "from" = 0x69cA348Bd928A158ADe7aa193C133f315803b06e   -- LitePSM jar
    AND "to"   = 0x0000000000000000000000000000000000000000
    AND contract_address IN (0x6b175474e89094c44da98b954eedeac495271d0f,   -- DAI
                             0xdc035d45d973e3ec169d2276ddab16f1e407384f)   -- USDS
    AND evt_block_date >= DATE '{{month_end_excl}}'
    AND evt_block_date <  DATE '{{burn_end_excl}}'
    AND evt_block_number <= {{pin_block}}
),

-- ── income: stability fees at fold ──────────────────────────────────────────
vat_events AS (
  SELECT i, call_block_number AS bn, call_tx_index AS txi, call_trace_address AS tr,
         CAST(dart AS DECIMAL(38,0)) AS dart, CAST(NULL AS DECIMAL(38,0)) AS rate,
         call_block_date AS d
  FROM maker_ethereum.vat_call_frob
  WHERE call_success AND i IN (SELECT ilk FROM fee_ilks)
    AND call_block_number <= {{pin_block}}
  UNION ALL
  SELECT i, call_block_number, call_tx_index, call_trace_address,
         CAST(dart AS DECIMAL(38,0)), CAST(NULL AS DECIMAL(38,0)), call_block_date
  FROM maker_ethereum.vat_call_grab
  WHERE call_success AND i IN (SELECT ilk FROM fee_ilks)
    AND call_block_number <= {{pin_block}}
  UNION ALL
  SELECT i, call_block_number, call_tx_index, call_trace_address,
         CAST(0 AS DECIMAL(38,0)), CAST(rate AS DECIMAL(38,0)), call_block_date
  FROM maker_ethereum.vat_call_fold
  WHERE call_success AND i IN (SELECT ilk FROM fee_ilks)
    AND call_block_number <= {{pin_block}}
),
vat_running AS (
  SELECT i, d, rate,
         SUM(dart) OVER (PARTITION BY i ORDER BY bn, txi, tr
                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS art
  FROM vat_events
),
fees AS (
  SELECT l.label,
         SUM(CAST(r.art AS DOUBLE) / 1e18 * CAST(r.rate AS DOUBLE) / 1e27) AS amount
  FROM vat_running r JOIN fee_ilks l ON l.ilk = r.i
  WHERE r.rate IS NOT NULL
    AND r.d >= DATE '{{month_start}}' AND r.d < DATE '{{month_end_excl}}'
  GROUP BY 1
),

-- ── expense: savings drips ──────────────────────────────────────────────────
susds AS (
  SELECT CAST(SUM(CAST(diff AS DECIMAL(38,0))) AS DOUBLE) / 1e18 AS amount
  FROM sky_ethereum.susds_evt_drip
  WHERE evt_block_date >= DATE '{{month_start}}' AND evt_block_date < DATE '{{month_end_excl}}'
    AND evt_block_number <= {{pin_block}}
),
stusds AS (
  SELECT CAST(SUM(CAST(diff AS DECIMAL(38,0))) AS DOUBLE) / 1e18 AS amount
  FROM sky_ethereum.stusds_evt_drip
  WHERE evt_block_date >= DATE '{{month_start}}' AND evt_block_date < DATE '{{month_end_excl}}'
    AND evt_block_number <= {{pin_block}}
),
dsr AS (
  SELECT CAST(SUM(CAST(rad AS DOUBLE)) / 1e45 AS DOUBLE) AS amount
  FROM maker_ethereum.vat_call_suck
  WHERE v = 0x197e90f9fad81970ba7976f33cbd77088e5d7cf7           -- MCD_POT
    AND call_success
    AND call_block_date >= DATE '{{month_start}}' AND call_block_date < DATE '{{month_end_excl}}'
    AND call_block_number <= {{pin_block}}
),

-- ── expense deduction: SSR on prime-held sUSDS ──────────────────────────────
prime_xfers AS (
  SELECT evt_block_date AS d, h.label,
         SUM(CASE WHEN "to" = h.addr THEN CAST(value AS DECIMAL(38,0))
                  ELSE -CAST(value AS DECIMAL(38,0)) END) AS net
  FROM erc20_ethereum.evt_transfer x
  JOIN prime_holders h ON h.addr IN (x."to", x."from")
  WHERE contract_address = 0xa3931d71877c0e7a3148cb7eb4463524fec27fbd   -- sUSDS
    AND "to" <> "from"
    AND evt_block_number <= {{pin_block}}
  GROUP BY 1, 2
),
carve_days AS (
  SELECT s.d
  FROM UNNEST(SEQUENCE(DATE '{{month_start}}' - INTERVAL '1' DAY,
                       DATE '{{month_end_excl}}' - INTERVAL '1' DAY)) AS s(d)
),
prime_bal AS (   -- EOD sUSDS shares per holder per day (running sum, full history)
  SELECT dd.d, hh.label,
         COALESCE((SELECT SUM(x.net) FROM prime_xfers x
                   WHERE x.label = hh.label AND x.d <= dd.d), 0) AS shares
  FROM carve_days dd CROSS JOIN prime_holders hh
),
chi_eod AS (
  SELECT d.d,
         (SELECT MAX_BY(CAST(chi AS DECIMAL(38,0)), (evt_block_number, evt_index))
          FROM sky_ethereum.susds_evt_drip e
          WHERE e.evt_block_date <= d.d AND e.evt_block_number <= {{pin_block}}) AS chi
  FROM carve_days d
),
chi_daily AS (
  SELECT c.d, c.chi - LAG(c.chi) OVER (ORDER BY c.d) AS dchi FROM chi_eod c
),
prime_carve AS (
  SELECT b.label,
         SUM(CAST(b.shares AS DOUBLE) / 1e18 * CAST(dl.dchi AS DOUBLE) / 1e27) AS amount
  FROM chi_daily dl
  JOIN prime_bal b ON b.d = dl.d - INTERVAL '1' DAY
  WHERE dl.dchi IS NOT NULL AND dl.d >= DATE '{{month_start}}'
  GROUP BY 1
  HAVING SUM(CAST(b.shares AS DOUBLE) / 1e18 * CAST(dl.dchi AS DOUBLE) / 1e27) <> 0
)

SELECT 'income:psm_jar' AS stream, CAST(d AS VARCHAR) AS label, d AS event_date, amount
FROM jar_burns
UNION ALL
SELECT 'income:stability_fee', label, CAST(NULL AS DATE), amount FROM fees
UNION ALL
SELECT 'expense:susds_drip', 'sUSDS SSR (gross, all holders)', CAST(NULL AS DATE),
       COALESCE(amount, 0) FROM susds
UNION ALL
SELECT 'expense:susds_prime', label, CAST(NULL AS DATE), amount FROM prime_carve
UNION ALL
SELECT 'expense:dsr_drip', 'DSR (pot)', CAST(NULL AS DATE), COALESCE(amount, 0) FROM dsr
UNION ALL
SELECT 'expense:stusds_drip', 'stUSDS', CAST(NULL AS DATE), COALESCE(amount, 0) FROM stusds
ORDER BY 1, 2
