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
-- Pattern-A JOIN matches zero rows). Verified on txs 0x0af33386…be492
-- (Jul 13 2026, 1,425,596.0044 aHorRwaRLUSD) and 0xf960709c…6ec9b (Jul 21
-- 2026, 42,585.4312 aHorRwaRLUSD) — before Pattern B existed here, those
-- two claims were bucketed as principal inflow and Grove's Jul 2026 E1
-- revenue was understated by $1,468,181.4356.
--
-- Pattern B amounts come from the RECEIPT — the aToken ``Transfer``
-- Distributor → ALM — gated on a same-tx ``Claimed`` marker, NOT from
-- ``Claimed.amount``. Two invariants this preserves from the Pattern-A
-- JOIN:
--   1. Receipt verification: a claim routed to an operator / alternate
--      recipient (Merkl supports both) books $0 here — we never credit
--      revenue the ALM did not receive.
--   2. Denomination: ``Claimed.amount`` is denominated in the *reward
--      token's* units (for wrapper campaigns that means wrapper shares,
--      not aToken units). The Transfer amount is in the venue aToken's
--      own units by construction, so a mis-registered campaign can't
--      misprice the venue.
--
-- Leg disjointness. The legs split on ``Claimed.token``:
--   * ``direct_claims`` counts receipt Transfers only for claims where
--     ``Claimed.token = {{atoken}}``;
--   * ``wrapper_claims`` excludes those rows (``reward_token <> atoken``),
--     so a same-venue claim can never hit both legs.
-- NOTE the wrapper Transfer path is NOT a usable discriminator: wrapper
-- campaigns ALSO transfer the reward (wrapper) token Distributor → ALM
-- before the in-tx redeem, so "reward token moved Distributor → ALM"
-- holds for BOTH patterns (a same-tx exclusion keyed on it zeroes
-- legitimate Feb/Apr claims — verified empirically). Cross-venue: a
-- direct claim of aToken Y enters venue X's Pattern-A candidates only if
-- Y itself appears as ``Mint.caller`` on X — i.e. the aToken Y contract
-- called ``pool.supply`` — which Aave V3 aTokens never do; the invariant
-- is pinned empirically by the Jul-2026 E3 = $0 row in
-- ``tests/integration/test_merkl_claims_e2e.py`` (July's direct E1
-- claims must not leak into E3).
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
--   Transfer(address indexed from, address indexed to, uint256 value)
--     topic0 = 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef
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
--   {{distributor}}             varbinary  — Merkl Distributor contract address.
--   {{distributor_padded_hex}}  text       — 64-char hex (NO ``0x`` prefix) of
--                                            the 32-byte left-padded Distributor
--                                            address, for indexed-topic
--                                            comparison (Transfer ``from``).
--   {{user_padded_hex}}         text       — 64-char hex (NO ``0x`` prefix) of
--                                            the 32-byte left-padded ALM address.
--   {{atoken}}                  varbinary  — The Aave aToken contract address
--                                            for the venue.
--   {{atoken_padded_hex}}       text       — 64-char hex (NO ``0x`` prefix) of
--                                            the 32-byte left-padded aToken
--                                            address, matched against
--                                            ``Claimed.topic2``.
--   {{start_date}}              text       — 'YYYY-MM-DD' (inclusive).
--   {{end_date}}                text       — 'YYYY-MM-DD' (inclusive).
--   {{pin_block}}               number     — upper-bound block_number (also the
--                                            cache key).
--
-- Output: single row, single column ``total_amount_raw`` — the sum of
-- matching amounts as uint256 in the aToken's raw decimals. Python
-- converts to USD via ``venue.token.decimals`` (assumes par-stable
-- underlying — same constraint as the Transfer-based helper, enforced
-- upstream in ``_atoken_external_revenue_usd``).

WITH claims AS (
  -- All Claimed(user = ALM) events from the Distributor in the window.
  -- Single base filter shared by both legs — a date / pin edit here can't
  -- desynchronise them.
  SELECT
    c.tx_hash,
    c.topic2                     AS reward_token,       -- 32-byte padded Claimed.token
    varbinary_to_uint256(c.data) AS claimed_amount_raw
  FROM ethereum.logs c
  WHERE c.contract_address = {{distributor}}
    AND c.topic0 = 0xf7a40077ff7a04c7e61f6f26fb13774259ddf1b6bce9ecf26a8276cdd3992683
    AND c.topic1 = from_hex('{{user_padded_hex}}')
    AND c.block_date >= DATE '{{start_date}}'
    AND c.block_date <= DATE '{{end_date}}'
    AND c.block_number <= {{pin_block}}
),

wrapper_claims AS (
  -- Pattern A: Claimed.token = staticAToken wrapper; attribute via the
  -- same-tx aToken Mint with caller = wrapper, onBehalfOf = ALM. The
  -- Claimed amount is authoritative here (verified to the cent against
  -- Grove's Feb/Apr claim amounts).
  SELECT cl.claimed_amount_raw AS amount_raw
  FROM claims cl
  INNER JOIN ethereum.logs m
    ON m.tx_hash = cl.tx_hash
   AND m.topic1  = cl.reward_token
  -- A claim OF this venue's aToken is Pattern B by definition (even one
  -- whose receipt was routed away from the ALM and books $0 there) —
  -- never a wrapper-redeem candidate.
  WHERE cl.reward_token <> from_hex('{{atoken_padded_hex}}')
    AND m.contract_address = {{atoken}}
    AND m.topic0 = 0x458f5fa412d0f69b08dd84872b0215675cc67bc1d5b6fd93300a1c3878b86196
    AND m.topic2 = from_hex('{{user_padded_hex}}')
    AND m.block_date >= DATE '{{start_date}}'
    AND m.block_date <= DATE '{{end_date}}'
),

direct_claims AS (
  -- Pattern B: sum the RECEIPT Transfers (this venue's aToken, Distributor
  -- → ALM) in txs carrying a Claimed marker for this aToken. See header
  -- for why the Transfer, not Claimed.amount, is the value source.
  SELECT varbinary_to_uint256(t.data) AS amount_raw
  FROM ethereum.logs t
  WHERE t.contract_address = {{atoken}}
    AND t.topic0 = 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef
    AND t.topic1 = from_hex('{{distributor_padded_hex}}')
    AND t.topic2 = from_hex('{{user_padded_hex}}')
    AND t.block_date >= DATE '{{start_date}}'
    AND t.block_date <= DATE '{{end_date}}'
    AND t.block_number <= {{pin_block}}
    AND EXISTS (
      SELECT 1 FROM claims cl
      WHERE cl.tx_hash = t.tx_hash
        AND cl.reward_token = from_hex('{{atoken_padded_hex}}')
    )
)

SELECT COALESCE(SUM(amount_raw), 0) AS total_amount_raw
FROM (
  SELECT amount_raw FROM wrapper_claims
  UNION ALL
  SELECT amount_raw FROM direct_claims
) AS all_claims
