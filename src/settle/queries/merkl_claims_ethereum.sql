-- Sum of Merkl-distributed aToken rewards arriving at the ALM during the
-- period, attributed per aToken. Merkl campaigns have paid Grove through
-- TWO distinct mechanics, both captured below:
--
-- Pattern A — staticAToken wrapper redeem (Feb 6 / Apr 24 2026 claims).
-- ``Claimed(user, token, amount)`` records ``token`` as the *Merkl reward
-- token* (Aave's staticAToken / LM wrapper, e.g. 0x72eeed8043… for
-- aEthRLUSD), NOT the underlying aToken the ALM ends up holding (e.g.
-- 0xfa82580c… aEthRLUSD itself). Filtering on the aToken address against
-- ``Claimed.topic2`` returns zero rows. The wrapper redeems to the aToken
-- inside the same tx via Aave's ``mint``, so the aToken contract emits
-- ``Mint(caller, onBehalfOf, value, …)`` where:
--   * ``contract_address`` = the aToken (what we want)
--   * ``caller``    (topic1) = the staticAToken (= ``Claimed.token``)
--   * ``onBehalfOf`` (topic2) = the ALM (= ``Claimed.user``)
-- Pairing ``(c.tx_hash, c.topic2) == (m.tx_hash, m.topic1)`` AND
-- ``m.contract_address = {{atoken}}`` deterministically routes each Claimed
-- amount to its venue. When a single tx claims rewards for multiple aTokens
-- (the Feb 6 tx claims for BOTH aHorRwaRLUSD and aEthRLUSD), each Claimed
-- row joins to exactly one Mint row — no double counting. Verified on tx
-- 0x8a81d6dd…704a (Feb 6 2026, two claim events for Grove).
--
-- Pattern B — direct aToken payout (Jul 13 / Jul 21 2026 claims).
-- The campaign funder deposits the aToken itself into the Distributor,
-- which pays out via an ordinary aToken Transfer — ``Claimed.token`` IS
-- the aToken and NO wrapper-redeem ``Mint`` fires (the only Mint events in
-- the tx are Aave interest accruals with ``caller = Distributor``, so the
-- Pattern-A JOIN matches zero rows). Detected by ``Claimed.topic2 =
-- atoken`` directly. Verified on txs 0x0af33386…be492 (Jul 13 2026,
-- 1,425,596.00 aHorRwaRLUSD) and 0xf960709c…6ec9b (Jul 21 2026,
-- 42,585.43 aHorRwaRLUSD) — before Pattern B existed here, those two
-- claims were bucketed as principal inflow and Grove's Jul 2026 E1
-- revenue was understated by $1,468,181.44.
--
-- The two legs are provably disjoint: Pattern A explicitly excludes
-- ``Claimed.topic2 = atoken`` rows (they belong to Pattern B), so a claim
-- can never be counted twice even if a pathological tx contained both a
-- direct payout and an aToken Mint with ``caller = atoken``.
--
-- Event signatures
-- ----------------
--   Claimed(address indexed user, address indexed token, uint256 amount)
--     topic0 = 0xf7a40077ff7a04c7e61f6f26fb13774259ddf1b6bce9ecf26a8276cdd3992683
--
--   Mint(address indexed caller, address indexed onBehalfOf, uint256 value,
--        uint256 balanceIncrease, uint256 index)              (Aave V3 aToken)
--     topic0 = 0x458f5fa412d0f69b08dd84872b0215675cc67bc1d5b6fd93300a1c3878b86196
--
-- Direct-sweep flows (Anchorage interest, BUIDL yield mints) don't fire a
-- Merkl Claimed event and are routed through the generic Transfer-based
-- ``atoken_external_inflow.sql`` instead (dispatched upstream).
--
-- Per-chain SQL because Dune doesn't allow ``{{param}}`` in ``FROM``. Add a
-- sibling file (``merkl_claims_<chain>.sql``) if Merkl ever lands on a
-- non-Ethereum chain a prime cares about.
--
-- Parameters
-- ----------
--   {{distributor}}        varbinary  — Merkl Distributor contract address.
--   {{user_padded_hex}}    text       — 64-char hex (NO ``0x`` prefix) of the
--                                       32-byte left-padded ALM address. Used
--                                       for indexed-topic comparison.
--   {{atoken}}             varbinary  — The Aave aToken contract address for
--                                       the venue. Joins on Mint event's
--                                       ``contract_address`` (Pattern A).
--   {{atoken_padded_hex}}  text       — 64-char hex (NO ``0x`` prefix) of the
--                                       32-byte left-padded aToken address.
--                                       Matched against ``Claimed.topic2``
--                                       (Pattern B / Pattern A exclusion).
--   {{start_date}}         text       — 'YYYY-MM-DD' (inclusive).
--   {{end_date}}           text       — 'YYYY-MM-DD' (inclusive).
--   {{pin_block}}          number     — upper-bound block_number (also the cache key).
--
-- Output: single row, single column ``total_amount_raw`` — the sum of
-- matching ``Claimed.amount`` values as uint256 in the aToken's raw
-- decimals. Python converts to USD via ``venue.token.decimals`` (assumes
-- par-stable underlying — same constraint as the Transfer-based helper,
-- enforced upstream in ``_atoken_external_revenue_usd``).

WITH wrapper_claims AS (
  -- Pattern A: Claimed.token = staticAToken wrapper; attribute via the
  -- same-tx aToken Mint with caller = wrapper, onBehalfOf = ALM.
  SELECT varbinary_to_uint256(c.data) AS amount_raw
  FROM ethereum.logs c
  INNER JOIN ethereum.logs m
    ON c.tx_hash = m.tx_hash
   AND m.topic1  = c.topic2
  WHERE c.contract_address = {{distributor}}
    AND c.topic0 = 0xf7a40077ff7a04c7e61f6f26fb13774259ddf1b6bce9ecf26a8276cdd3992683
    AND c.topic1 = from_hex('{{user_padded_hex}}')
    AND c.topic2 <> from_hex('{{atoken_padded_hex}}')   -- direct payouts → Pattern B
    AND c.block_date >= DATE '{{start_date}}'
    AND c.block_date <= DATE '{{end_date}}'
    AND c.block_number <= {{pin_block}}
    AND m.contract_address = {{atoken}}
    AND m.topic0 = 0x458f5fa412d0f69b08dd84872b0215675cc67bc1d5b6fd93300a1c3878b86196
    AND m.topic2 = from_hex('{{user_padded_hex}}')
    AND m.block_date >= DATE '{{start_date}}'
    AND m.block_date <= DATE '{{end_date}}'
),

direct_claims AS (
  -- Pattern B: Claimed.token IS the aToken — the Distributor transfers its
  -- own aToken balance to the ALM; no wrapper Mint fires. No JOIN needed:
  -- ``Claimed.topic2 = atoken`` already attributes the claim to this venue.
  SELECT varbinary_to_uint256(c.data) AS amount_raw
  FROM ethereum.logs c
  WHERE c.contract_address = {{distributor}}
    AND c.topic0 = 0xf7a40077ff7a04c7e61f6f26fb13774259ddf1b6bce9ecf26a8276cdd3992683
    AND c.topic1 = from_hex('{{user_padded_hex}}')
    AND c.topic2 = from_hex('{{atoken_padded_hex}}')
    AND c.block_date >= DATE '{{start_date}}'
    AND c.block_date <= DATE '{{end_date}}'
    AND c.block_number <= {{pin_block}}
)

SELECT COALESCE(SUM(amount_raw), 0) AS total_amount_raw
FROM (
  SELECT amount_raw FROM wrapper_claims
  UNION ALL
  SELECT amount_raw FROM direct_claims
) AS all_claims
