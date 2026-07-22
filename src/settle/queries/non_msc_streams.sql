-- Non-MSC Sky protocol P&L — all streams for one calendar month, one execution.
--
-- Methodology: https://hackmd.io/@W57nO5PyRMKhcLqjvsLifw/S1zxTDpXMg
-- (validated May 2026: every line reproduced to the dollar — see PRD §17.13).
--
-- Parameters:
--   {{month_start}}      date  — first day of the reported month
--   {{month_end_excl}}   date  — first day of the NEXT month
--   {{pin_block}}        number — upper block cutoff (≥ the last block of the month)
--
-- Output rows: (stream, label, event_date, amount) —
--   income:psm_jar       one row per jar burn that LANDS in the calendar month
--                        [month_start, month_end_excl) — cash / transfer-date
--                        basis. Compute sums ALL of them (a month can have >1,
--                        e.g. Jan 2026: December's on-slot burn + November's
--                        late burn, both landing in January).
--   income:stability_fee one row per ilk, ACCRUAL basis: Σ over checkpoints
--                        (month bounds + every in-month Art change) of
--                        Art(t_i) × (r_true(t_{i+1}) − r_true(t_i)), where
--                        r_true(t) = rate(last fold ≤ t) × duty^(t − rho) is
--                        reconstructed from `duty` directly (NOT from when
--                        jug.drip/vat.fold happened to fire). See §"accrual
--                        basis" note below.
--   income:liq_owe       Σ owe over clip.take in the month (rad → USDS)
--   income:liq_due       Σ due over dog.bark in the month; liquidation revenue
--                        = owe − due (realized penalty; negative if under-recovered)
--   income:surplus_return one row per join→vow move NOT attributable to the
--                        PSM/RWA jar (vest-budget refunds in 2026)
--   income:rwa_void      Σ join→vow moves whose tx voided a legacy-RWA jar
--                        (tripwire — ~0 in 2026)
--   expense:susds_drip   sUSDS SSR recognized at drip (gross, all holders)
--   expense:susds_prime  SSR accrued to PRIME-held sUSDS (ALM + subproxy
--                        holders) — already netted inside MSC via BR − 30bps /
--                        agent rate; INFORMATIONAL split (NOT deducted — gross
--                        stays in, the MSC leg carries the offsetting BR income)
--   expense:dsr_drip     legacy DSR (vat.suck minting to the pot)
--   expense:stusds_drip  stUSDS staking interest recognized at drip
--   expense:liq_coin     Σ coin over clip kicks + redos (keeper incentives)
--   expense:vest         Σ amt over DssVest suckable payouts (DAI + USDS vests)
--
-- Precision: drip/burn lines are exact DECIMAL sums ÷ 1e18. The fee and
-- prime-carve-out lines multiply 1e27-scale factors and use DOUBLE (relative
-- error ~1e-15, sub-cent on $M lines; validated to the dollar vs the target).
--
-- Accrual basis (stability fees): an ilk's debt earns interest continuously at
-- its jug `duty`; the chain only records it when someone calls jug.drip →
-- vat.fold, which can lag hours (ETH ilks) or YEARS (legacy RWA). Booking at
-- fold slushes that catch-up into whatever month the keeper poked. Instead we
-- reconstruct the true rate index r_true(t) = rate(last fold ≤ t) ×
-- duty^(t − rho_lastfold) and integrate Art × Δr_true across the month. This
-- needs NO future fold — r_true is projected from the last fold + duty (both
-- past data) and lands exactly on the next fold when it fires. Exact given two
-- mainnet invariants: (1) every duty change ships with a drip in the same
-- spell, so duty is constant across each inter-fold interval and r_true never
-- crosses a duty change; (2) jug.base has always been 0. Validated May/Jun
-- 2026 to the dollar against BA Labs' published per-ilk P&L (see PRD §17.13).

WITH fee_ilks AS (
  -- The full ilk universe (every vat.init since genesis) MINUS the prime-side
  -- and defunct prefixes: ALLOCATOR-%/DIRECT-% are the MSC (prime) BR side,
  -- PSM-%/TELEPORT% are defunct. This leaves the 9 core-vault ilks (ETH-A/B/C,
  -- WSTETH-A/B, WBTC-A/B/C, LSEV2-SKY-A) plus the fee-earning legacy RWA ilks
  -- (2026: RWA002-A ~$200K/mo, RWA004-A/RWA005-A ~$10K/mo each — the latter
  -- two never drip, so the accrual basis is what surfaces them at all).
  -- Future onboardings are included automatically; no whitelist.
  SELECT DISTINCT ilk FROM maker_ethereum.vat_call_init
  WHERE call_success
    AND from_utf8(ilk) NOT LIKE 'ALLOCATOR-%'
    AND from_utf8(ilk) NOT LIKE 'DIRECT-%'
    AND from_utf8(ilk) NOT LIKE 'PSM-%'
    AND from_utf8(ilk) NOT LIKE 'TELEPORT%'
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
  SELECT evt_block_date AS d, evt_tx_hash AS tx,
         CAST(CAST(value AS DECIMAL(38,0)) AS DOUBLE) / 1e18 AS amount
  FROM erc20_ethereum.evt_transfer
  WHERE "from" = 0x69cA348Bd928A158ADe7aa193C133f315803b06e   -- LitePSM jar
    AND "to"   = 0x0000000000000000000000000000000000000000
    AND contract_address IN (0x6b175474e89094c44da98b954eedeac495271d0f,   -- DAI
                             0xdc035d45d973e3ec169d2276ddab16f1e407384f)   -- USDS
    AND evt_block_date >= DATE '{{month_start}}'
    AND evt_block_date <  DATE '{{month_end_excl}}'
    AND evt_block_number <= {{pin_block}}
),

-- ── income: stability fees, ACCRUAL basis ───────────────────────────────────
-- Per ilk: reconstruct r_true(t) and integrate Art × Δr_true over the month.
folds AS (
  SELECT i, CAST(call_block_time AS TIMESTAMP) AS t,
         call_block_number AS bn, call_tx_index AS txi, call_trace_address AS tr,
         CAST(rate AS DECIMAL(38,0)) AS drate
  FROM maker_ethereum.vat_call_fold
  WHERE call_success AND i IN (SELECT ilk FROM fee_ilks) AND call_block_number <= {{pin_block}}
),
duty_hist AS (   -- per-ilk jug `duty` (RAY per-second) over time
  SELECT ilk AS i, CAST(call_block_time AS TIMESTAMP) AS t,
         CAST(data_uint256 AS DOUBLE) AS duty
  FROM maker_ethereum.jug_call_file
  WHERE call_success AND from_utf8(what) LIKE 'duty%'
    AND ilk IN (SELECT ilk FROM fee_ilks) AND call_block_number <= {{pin_block}}
),
art_events AS (   -- every Art change (draw / repay / liquidation seizure)
  SELECT i, CAST(call_block_time AS TIMESTAMP) AS t,
         call_block_number AS bn, call_tx_index AS txi, call_trace_address AS tr,
         CAST(dart AS DECIMAL(38,0)) AS dart
  FROM maker_ethereum.vat_call_frob
  WHERE call_success AND i IN (SELECT ilk FROM fee_ilks) AND call_block_number <= {{pin_block}}
  UNION ALL
  SELECT i, CAST(call_block_time AS TIMESTAMP), call_block_number, call_tx_index, call_trace_address,
         CAST(dart AS DECIMAL(38,0))
  FROM maker_ethereum.vat_call_grab
  WHERE call_success AND i IN (SELECT ilk FROM fee_ilks) AND call_block_number <= {{pin_block}}
),
-- Carried-in state at month_start (cheap GROUP-BY aggregations over genesis —
-- keeps the per-row windowing below confined to the small in-month event set).
art0 AS (
  SELECT i, SUM(dart) AS art
  FROM art_events WHERE t < CAST(DATE '{{month_start}}' AS TIMESTAMP) GROUP BY i
),
fold0 AS (   -- rate accumulator (RAY + Σ prior deltas) & rho at last fold < month_start
  SELECT i, DECIMAL '1000000000000000000000000000' + SUM(drate) AS rate_abs, MAX(t) AS rho
  FROM folds WHERE t < CAST(DATE '{{month_start}}' AS TIMESTAMP) GROUP BY i
),
duty0 AS (   -- duty at month_start (last file ≤ rho0 == last file < month_start)
  SELECT i, duty FROM (
    SELECT i, duty, ROW_NUMBER() OVER (PARTITION BY i ORDER BY t DESC) AS rn
    FROM duty_hist WHERE t < CAST(DATE '{{month_start}}' AS TIMESTAMP)
  ) WHERE rn = 1
),
-- Unified rows: one seed row per ilk at month_start carrying the carried-in
-- state (art0 as initial dart, rate0 as initial rate delta), then the in-month
-- folds / Art-changes / duty files, then a month_end boundary row. The seed +
-- month-bound rows sort around same-second events via bn sentinels.
-- Columns: i, ts, bn, txi, tr, dart, rate_delta, rho_src, duty, is_ckpt
rows_u AS (
  SELECT f.i, CAST(DATE '{{month_start}}' AS TIMESTAMP) AS ts,
         CAST(-1 AS BIGINT) AS bn, 0 AS txi, CAST(ARRAY[] AS ARRAY(BIGINT)) AS tr,
         COALESCE(a.art, CAST(0 AS DECIMAL(38,0))) AS dart,
         f.rate_abs AS rate_delta, f.rho AS rho_src, d.duty AS duty, true AS is_ckpt
  FROM fold0 f
  LEFT JOIN art0 a ON a.i = f.i
  LEFT JOIN duty0 d ON d.i = f.i
  UNION ALL
  SELECT i, t, bn, txi, tr, CAST(0 AS DECIMAL(38,0)), drate, t, CAST(NULL AS DOUBLE), false
  FROM folds
  WHERE t >= CAST(DATE '{{month_start}}' AS TIMESTAMP) AND t < CAST(DATE '{{month_end_excl}}' AS TIMESTAMP)
  UNION ALL
  SELECT i, t, bn, txi, tr, dart, CAST(0 AS DECIMAL(38,0)), CAST(NULL AS TIMESTAMP), CAST(NULL AS DOUBLE), true
  FROM art_events
  WHERE t >= CAST(DATE '{{month_start}}' AS TIMESTAMP) AND t < CAST(DATE '{{month_end_excl}}' AS TIMESTAMP)
  UNION ALL
  SELECT i, t, 9223372036854775806, 0, CAST(ARRAY[] AS ARRAY(BIGINT)),
         CAST(0 AS DECIMAL(38,0)), CAST(0 AS DECIMAL(38,0)), CAST(NULL AS TIMESTAMP), duty, false
  FROM duty_hist
  WHERE t >= CAST(DATE '{{month_start}}' AS TIMESTAMP) AND t < CAST(DATE '{{month_end_excl}}' AS TIMESTAMP)
  UNION ALL
  SELECT fi.ilk, CAST(DATE '{{month_end_excl}}' AS TIMESTAMP), 9223372036854775807, 0,
         CAST(ARRAY[] AS ARRAY(BIGINT)), CAST(0 AS DECIMAL(38,0)), CAST(0 AS DECIMAL(38,0)),
         CAST(NULL AS TIMESTAMP), CAST(NULL AS DOUBLE), true
  FROM fee_ilks fi
),
filled AS (   -- running Art + rate accumulator + forward-filled (rho, duty)
  SELECT i, ts, bn, txi, tr, is_ckpt,
         SUM(dart)       OVER w AS art,
         SUM(rate_delta) OVER w AS rate_abs,
         LAST_VALUE(rho_src) IGNORE NULLS OVER w AS rho,
         LAST_VALUE(duty)    IGNORE NULLS OVER w AS duty
  FROM rows_u
  WINDOW w AS (PARTITION BY i ORDER BY ts, bn, txi, tr
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
),
ckpt_pick AS (   -- collapse to one row per (ilk, checkpoint ts): last-ordered state
  SELECT i, ts, art, rate_abs, rho, duty,
         ROW_NUMBER() OVER (PARTITION BY i, ts ORDER BY bn DESC, txi DESC, tr DESC) AS rn
  FROM filled WHERE is_ckpt
),
ckpt AS (   -- r_true(ts) = rate(last fold ≤ ts) × duty^(ts − rho)
  SELECT i, ts, art,
         CAST(rate_abs AS DOUBLE)
           * POWER(duty / 1e27, CAST(date_diff('second', rho, ts) AS DOUBLE)) AS rtrue
  FROM ckpt_pick
  WHERE rn = 1 AND rate_abs IS NOT NULL AND rho IS NOT NULL AND duty IS NOT NULL
),
fee_iv AS (   -- interval contribution: Art(t_i) × (r_true(t_{i+1}) − r_true(t_i))
  SELECT i, CAST(art AS DOUBLE) / 1e18
             * (LEAD(rtrue) OVER (PARTITION BY i ORDER BY ts) - rtrue) AS contrib
  FROM ckpt
),
fees AS (
  SELECT rtrim(from_utf8(i), U&'\0000') AS label, SUM(contrib) / 1e27 AS amount
  FROM fee_iv
  GROUP BY 1
  HAVING ABS(SUM(contrib) / 1e27) >= 0.01
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
-- L1 leg: direct sUSDS holdings at prime ALM/subproxy addresses.
-- L2 legs: prime-held bridged sUSDS (L2 ALM proxies + PSM3 reserves). The
-- canonical L1 tokens sit in bridge escrows, but the SSR claim belongs to
-- the L2 holders — prime L2 balances × the SAME global Δchi is their
-- accrual, and it is MSC-accounted (PSM3 appreciation, S37/S43/S47/S51
-- POL proxies), so it is carved out here alongside the L1 leg.
-- NOTE: L2 rows are cut by calendar DATE, not {{pin_block}} — L2 chains
-- have their own block numbering; dates are final once the month is past.
prime_l2_holders AS (
  SELECT * FROM (VALUES
    -- (chain, sUSDS token, holder, label) — from config/spark.yaml
    ('base',     0x5875eee11cf8398102fdad704c9e96607675467a,
                 0x2917956eff0b5eaf030abdb4ef4296df775009ca, 'spark_alm_base'),
    ('base',     0x5875eee11cf8398102fdad704c9e96607675467a,
                 0x1601843c5e9bc251a3272907010afa41fa18347e, 'spark_psm3_base'),
    ('arbitrum', 0xddb46999f8891663a8f2828d25298f70416d7610,
                 0x92afd6f2385a90e44da3a8b60fe36f6cbe1d8709, 'spark_alm_arbitrum'),
    ('arbitrum', 0xddb46999f8891663a8f2828d25298f70416d7610,
                 0x2b05f8e1cacc6974fd79a673a341fe1f58d27266, 'spark_psm3_arbitrum'),
    ('optimism', 0xb5b2dc7fd34c249f4be7fb1fcea07950784229e0,
                 0x876664f0c9ff24d1aa355ce9f1680ae1a5bf36fb, 'spark_alm_optimism'),
    ('optimism', 0xb5b2dc7fd34c249f4be7fb1fcea07950784229e0,
                 0xe0f9978b907853f354d79188a3defbd41978af62, 'spark_psm3_optimism'),
    ('unichain', 0xa06b10db9f390990364a3984c04fadf1c13691b5,
                 0x345e368fccd62266b3f5f37c9a131fd1c39f5869, 'spark_alm_unichain'),
    ('unichain', 0xa06b10db9f390990364a3984c04fadf1c13691b5,
                 0x7b42ed932f26509465f7ce3faf76ffce1275312f, 'spark_psm3_unichain')
  ) AS t(chain, token, addr, label)
),
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
  UNION ALL
  SELECT evt_block_date, h.label,
         SUM(CASE WHEN "to" = h.addr THEN CAST(value AS DECIMAL(38,0))
                  ELSE -CAST(value AS DECIMAL(38,0)) END)
  FROM erc20_base.evt_transfer x
  JOIN prime_l2_holders h ON h.chain = 'base' AND h.addr IN (x."to", x."from")
  WHERE contract_address = 0x5875eee11cf8398102fdad704c9e96607675467a
    AND "to" <> "from" AND evt_block_date < DATE '{{month_end_excl}}'
  GROUP BY 1, 2
  UNION ALL
  SELECT evt_block_date, h.label,
         SUM(CASE WHEN "to" = h.addr THEN CAST(value AS DECIMAL(38,0))
                  ELSE -CAST(value AS DECIMAL(38,0)) END)
  FROM erc20_arbitrum.evt_transfer x
  JOIN prime_l2_holders h ON h.chain = 'arbitrum' AND h.addr IN (x."to", x."from")
  WHERE contract_address = 0xddb46999f8891663a8f2828d25298f70416d7610
    AND "to" <> "from" AND evt_block_date < DATE '{{month_end_excl}}'
  GROUP BY 1, 2
  UNION ALL
  SELECT evt_block_date, h.label,
         SUM(CASE WHEN "to" = h.addr THEN CAST(value AS DECIMAL(38,0))
                  ELSE -CAST(value AS DECIMAL(38,0)) END)
  FROM erc20_optimism.evt_transfer x
  JOIN prime_l2_holders h ON h.chain = 'optimism' AND h.addr IN (x."to", x."from")
  WHERE contract_address = 0xb5b2dc7fd34c249f4be7fb1fcea07950784229e0
    AND "to" <> "from" AND evt_block_date < DATE '{{month_end_excl}}'
  GROUP BY 1, 2
  UNION ALL
  SELECT evt_block_date, h.label,
         SUM(CASE WHEN "to" = h.addr THEN CAST(value AS DECIMAL(38,0))
                  ELSE -CAST(value AS DECIMAL(38,0)) END)
  FROM erc20_unichain.evt_transfer x
  JOIN prime_l2_holders h ON h.chain = 'unichain' AND h.addr IN (x."to", x."from")
  WHERE contract_address = 0xa06b10db9f390990364a3984c04fadf1c13691b5
    AND "to" <> "from" AND evt_block_date < DATE '{{month_end_excl}}'
  GROUP BY 1, 2
),
carve_days AS (
  SELECT s.d
  FROM UNNEST(SEQUENCE(DATE '{{month_start}}' - INTERVAL '1' DAY,
                       DATE '{{month_end_excl}}' - INTERVAL '1' DAY)) AS s(d)
),
all_prime_labels AS (
  SELECT label FROM prime_holders
  UNION ALL
  SELECT label FROM prime_l2_holders
),
prime_bal AS (   -- EOD sUSDS shares per holder per day (running sum, full history)
  SELECT dd.d, hh.label,
         COALESCE((SELECT SUM(x.net) FROM prime_xfers x
                   WHERE x.label = hh.label AND x.d <= dd.d), 0) AS shares
  FROM carve_days dd CROSS JOIN all_prime_labels hh
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
),

-- ── liquidations (Liquidations 2.0) ─────────────────────────────────────────
-- Matched by RAW topic0 on ethereum.logs (the same hashes the HyperSync port
-- uses), so no decoded-table schema is assumed and every Clipper instance is
-- covered. Amounts are rad (1e45) → USDS via DOUBLE (relative err ~1e-15,
-- sub-cent). Clipper instances are discovered from the Dog's Bark `clip` field
-- so takes on auctions barked in a PRIOR month are still attributed here.
--   Bark(ilk,urn,ink,art,due,clip,id)  due=word[2]  clip=word[3][12:]
--   Take(id,max,price,owe,tab,lot,usr) owe=word[2]
--   Kick(id,top,tab,lot,usr,kpr,coin)  coin=word[3]   (indexed: id,usr,kpr)
--   Redo(id,top,tab,lot,usr,kpr,coin)  coin=word[3]
liq_clippers AS (
  SELECT DISTINCT bytearray_substring(data, 109, 20) AS clip
  FROM ethereum.logs
  WHERE contract_address = 0x135954d155898d42c90d2a57824c690e0c7bef1b   -- MCD_DOG
    AND topic0 = 0x85258d09e1e4ef299ff3fc11e74af99563f022d21f3f940db982229dc2a3358c  -- Bark
    AND block_number <= {{pin_block}}
),
liq_due AS (
  SELECT SUM(CAST(bytearray_to_uint256(bytearray_substring(data, 65, 32)) AS DOUBLE)) / 1e45 AS amount
  FROM ethereum.logs
  WHERE contract_address = 0x135954d155898d42c90d2a57824c690e0c7bef1b
    AND topic0 = 0x85258d09e1e4ef299ff3fc11e74af99563f022d21f3f940db982229dc2a3358c
    AND block_time >= CAST(DATE '{{month_start}}' AS TIMESTAMP)
    AND block_time <  CAST(DATE '{{month_end_excl}}' AS TIMESTAMP)
    AND block_number <= {{pin_block}}
),
liq_owe AS (
  SELECT SUM(CAST(bytearray_to_uint256(bytearray_substring(data, 65, 32)) AS DOUBLE)) / 1e45 AS amount
  FROM ethereum.logs
  WHERE topic0 = 0x05e309fd6ce72f2ab888a20056bb4210df08daed86f21f95053deb19964d86b1  -- Take
    AND contract_address IN (SELECT clip FROM liq_clippers)
    AND block_time >= CAST(DATE '{{month_start}}' AS TIMESTAMP)
    AND block_time <  CAST(DATE '{{month_end_excl}}' AS TIMESTAMP)
    AND block_number <= {{pin_block}}
),
liq_coin AS (
  SELECT SUM(CAST(bytearray_to_uint256(bytearray_substring(data, 97, 32)) AS DOUBLE)) / 1e45 AS amount
  FROM ethereum.logs
  WHERE topic0 IN (0x7c5bfdc0a5e8192f6cd4972f382cec69116862fb62e6abff8003874c58e064b8,  -- Kick
                   0x275de7ecdd375b5e8049319f8b350686131c219dd4dc450a08e9cf83b03c865f)  -- Redo
    AND contract_address IN (SELECT clip FROM liq_clippers)
    AND block_time >= CAST(DATE '{{month_start}}' AS TIMESTAMP)
    AND block_time <  CAST(DATE '{{month_end_excl}}' AS TIMESTAMP)
    AND block_number <= {{pin_block}}
),

-- ── vest (gross suckable DssVest payouts) ───────────────────────────────────
-- Vest(uint256 indexed id, uint256 amt); amt = data word[0], /1e18. Covers the
-- DAI + USDS + legacy-DAI vest contracts (token vests never touch the vow —
-- excluded). Booked gross at call time (the refund half lands in Surplus below).
vest AS (
  SELECT SUM(CAST(bytearray_to_uint256(bytearray_substring(data, 1, 32)) AS DOUBLE)) / 1e18 AS amount
  FROM ethereum.logs
  WHERE contract_address IN (0xa4c22f0e25C6630B2017979AcF1f865e94695C4b,   -- MCD_VEST_DAI
                             0xc447a9745aDe9A44Bb9E37B7F6C92f9582544110,   -- MCD_VEST_USDS
                             0x2Cc583c0AaCDaC9e23CB601fDA8F1A0c56Cdcb71)   -- MCD_VEST_DAI_LEGACY
    AND topic0 = 0xa2906882572b0e9dfe893158bb064bc308eb1bd87d1da481850f9d17fc293847  -- Vest
    AND block_time >= CAST(DATE '{{month_start}}' AS TIMESTAMP)
    AND block_time <  CAST(DATE '{{month_end_excl}}' AS TIMESTAMP)
    AND block_number <= {{pin_block}}
),

-- ── surplus returns / RWA jar voids (join → vow) ────────────────────────────
-- Cash paid straight into the surplus buffer: {dai,usds}Join.join(vow, wad),
-- on-chain as Vat.move(join → vow). The Vat `note` modifier logs
-- topic1=arg1(src), topic2=arg2(dst), topic3=arg3(rad) — so rad is topic3
-- directly (no calldata decode). This mechanism is SHARED with the LitePSM jar
-- payment (routes to the PSM line) and RWA jar voids (routes to the RWA line),
-- so each move is classified by its transaction's burn source:
--   tx has a LitePSM jar burn  → PSM (already booked as income:psm_jar)
--   tx has an RWA jar transfer → income:rwa_void
--   otherwise                  → income:surplus_return
vow_moves AS (
  SELECT tx_hash, block_date AS d,
         CAST(bytearray_to_uint256(topic3) AS DOUBLE) / 1e45 AS amount
  FROM ethereum.logs
  WHERE contract_address = 0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B   -- Vat
    AND topic0 = 0xbb35783b00000000000000000000000000000000000000000000000000000000  -- move sig
    AND topic1 IN (0x0000000000000000000000009759a6ac90977b93b58547b4a71c78317f391a28,  -- MCD_JOIN_DAI
                   0x0000000000000000000000003c0f895007ca717aa01c8693e59df1e8c3777feb)  -- USDS_JOIN
    AND topic2 = 0x000000000000000000000000a950524441892a31ebddf91d3ceefa04bf454466       -- MCD_VOW
    AND block_time >= CAST(DATE '{{month_start}}' AS TIMESTAMP)
    AND block_time <  CAST(DATE '{{month_end_excl}}' AS TIMESTAMP)
    AND block_number <= {{pin_block}}
),
rwa_void_txs AS (   -- txs in which a known RWA jar moved stablecoins (void())
  SELECT DISTINCT evt_tx_hash AS tx
  FROM erc20_ethereum.evt_transfer
  WHERE "from" IN (0xef1B095F700BE471981aae025f92B03091c3AD47,   -- RWA007_A_JAR
                   0x6C6d4Be2223B5d202263515351034861dD9aFdb6,   -- RWA009_A_JAR (H.V.Bank)
                   0x71eC6d5Ee95B12062139311CA1fE8FD698Cbe0Cf,   -- RWA014_A_JAR
                   0xc27C3D3130563C1171feCC4F76C217Db603997cf)   -- RWA015_A_JAR
    AND evt_block_date >= DATE '{{month_start}}'
    AND evt_block_date <  DATE '{{month_end_excl}}'
    AND evt_block_number <= {{pin_block}}
),
surplus AS (
  SELECT d, amount FROM vow_moves
  WHERE tx_hash NOT IN (SELECT tx FROM jar_burns)      -- PSM jar → PSM line
    AND tx_hash NOT IN (SELECT tx FROM rwa_void_txs)   -- RWA jar → RWA line
),
rwa_void AS (
  SELECT COALESCE(SUM(amount), 0) AS amount FROM vow_moves
  WHERE tx_hash IN (SELECT tx FROM rwa_void_txs)
)

SELECT 'income:psm_jar' AS stream, CAST(d AS VARCHAR) AS label, d AS event_date, amount
FROM jar_burns
UNION ALL
SELECT 'income:stability_fee', label, CAST(NULL AS DATE), amount FROM fees
UNION ALL
SELECT 'income:liq_owe', 'liquidation owe (takes)', CAST(NULL AS DATE), COALESCE(amount, 0) FROM liq_owe
UNION ALL
SELECT 'income:liq_due', 'liquidation due (barks)', CAST(NULL AS DATE), COALESCE(amount, 0) FROM liq_due
UNION ALL
SELECT 'income:surplus_return', CAST(d AS VARCHAR), d, amount FROM surplus
UNION ALL
SELECT 'income:rwa_void', 'RWA jars (void)', CAST(NULL AS DATE), COALESCE(amount, 0) FROM rwa_void
UNION ALL
SELECT 'expense:susds_drip', 'sUSDS SSR (gross, all holders)', CAST(NULL AS DATE),
       COALESCE(amount, 0) FROM susds
UNION ALL
SELECT 'expense:susds_prime', label, CAST(NULL AS DATE), amount FROM prime_carve
UNION ALL
SELECT 'expense:dsr_drip', 'DSR (pot)', CAST(NULL AS DATE), COALESCE(amount, 0) FROM dsr
UNION ALL
SELECT 'expense:stusds_drip', 'stUSDS', CAST(NULL AS DATE), COALESCE(amount, 0) FROM stusds
UNION ALL
SELECT 'expense:liq_coin', 'keeper incentives (kicks + redos)', CAST(NULL AS DATE),
       COALESCE(amount, 0) FROM liq_coin
UNION ALL
SELECT 'expense:vest', 'vest (gross suckable)', CAST(NULL AS DATE), COALESCE(amount, 0) FROM vest
ORDER BY 1, 2
