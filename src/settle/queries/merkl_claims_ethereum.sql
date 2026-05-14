-- Sum of Merkl-distributed aToken rewards arriving at the ALM during the
-- period, attributed per aToken via a JOIN to the Aave V3 ``Mint`` event.
--
-- Why a JOIN instead of filtering ``Claimed.token``? Merkl's
-- ``Claimed(user, token, amount)`` event records ``token`` as the *Merkl
-- reward token* (Aave's staticAToken / LM wrapper, e.g. 0x72eeed8043… for
-- aEthRLUSD), NOT the underlying aToken the ALM ends up holding (e.g.
-- 0xfa82580c… aEthRLUSD itself). Filtering on the aToken address against
-- ``Claimed.topic2`` returns zero rows. Verified on tx
-- 0x8a81d6dd…704a (Feb 6 2026, two claim events for Grove).
--
-- The Aave V3 aToken contract emits ``Mint(caller, onBehalfOf, value, …)``
-- alongside the staticAToken's redeem inside the same tx, where:
--   * ``contract_address`` = the aToken (what we want)
--   * ``caller``    (topic1) = the staticAToken (= ``Claimed.token``)
--   * ``onBehalfOf`` (topic2) = the ALM (= ``Claimed.user``)
--
-- So pairing ``(c.tx_hash, c.topic2) == (m.tx_hash, m.topic1)`` AND
-- ``m.contract_address = {{atoken}}`` deterministically routes each Claimed
-- amount to its venue. When a single tx claims rewards for multiple aTokens
-- (the Feb 6 tx claims for BOTH aHorRwaRLUSD and aEthRLUSD), each Claimed
-- row joins to exactly one Mint row — no double counting.
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
-- The Merkl claim path falls through the staticAToken-LM wrapper, which
-- redeems to the underlying aToken via Aave's ``mint`` (hence the Mint
-- event). Direct-sweep flows (Anchorage interest, BUIDL yield mints) don't
-- fire a Merkl Claimed event and are routed through the generic Transfer-
-- based ``atoken_external_inflow.sql`` instead (dispatched upstream).
--
-- Per-chain SQL because Dune doesn't allow ``{{param}}`` in ``FROM``. Add a
-- sibling file (``merkl_claims_<chain>.sql``) if Merkl ever lands on a
-- non-Ethereum chain a prime cares about.
--
-- Parameters
-- ----------
--   {{distributor}}      varbinary  — Merkl Distributor contract address.
--   {{user_padded_hex}}  text       — 64-char hex (NO ``0x`` prefix) of the
--                                     32-byte left-padded ALM address. Used
--                                     for indexed-topic comparison.
--   {{atoken}}           varbinary  — The Aave aToken contract address for
--                                     the venue. Joins on Mint event's
--                                     ``contract_address`` to attribute the
--                                     Claimed amount to this venue.
--   {{start_date}}       text       — 'YYYY-MM-DD' (inclusive).
--   {{end_date}}         text       — 'YYYY-MM-DD' (inclusive).
--   {{pin_block}}        number     — upper-bound block_number (also the cache key).
--
-- Output: single row, single column ``total_amount_raw`` — the sum of
-- matching ``Claimed.amount`` values as uint256 in the aToken's raw
-- decimals. Python converts to USD via ``venue.token.decimals`` (assumes
-- par-stable underlying — same constraint as the Transfer-based helper,
-- enforced upstream in ``_atoken_external_revenue_usd``).

SELECT COALESCE(SUM(varbinary_to_uint256(c.data)), 0) AS total_amount_raw
FROM ethereum.logs c
INNER JOIN ethereum.logs m
  ON c.tx_hash = m.tx_hash
 AND m.topic1  = c.topic2
WHERE c.contract_address = {{distributor}}
  AND c.topic0 = 0xf7a40077ff7a04c7e61f6f26fb13774259ddf1b6bce9ecf26a8276cdd3992683
  AND c.topic1 = from_hex('{{user_padded_hex}}')
  AND c.block_date >= DATE '{{start_date}}'
  AND c.block_date <= DATE '{{end_date}}'
  AND c.block_number <= {{pin_block}}
  AND m.contract_address = {{atoken}}
  AND m.topic0 = 0x458f5fa412d0f69b08dd84872b0215675cc67bc1d5b6fd93300a1c3878b86196
  AND m.topic2 = from_hex('{{user_padded_hex}}')
  AND m.block_date >= DATE '{{start_date}}'
  AND m.block_date <= DATE '{{end_date}}'
