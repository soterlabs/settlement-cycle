# July Base withdrawals retain borrowed basis through native-bridge custody

The July 2, 2026 Spark Base spell removes excess liquidity by bridging the
ALM's complete USDS and sUSDS balances to the Ethereum ALM. It executed July 6;
both messages finalized on Ethereum July 13. The historical capital snapshot
recorded the burns and later receipts but did not connect them.

Exact spell source:
https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20260702/SparkBase_20260702.sol

| Token | Raw units (18 decimals) | Source USD value | Arrival USD value |
|---|---:|---:|---:|
| USDS | 146550618210418117475041461 | 146,550,618.210418 | 146,550,618.210418 |
| sUSDS | 191958411108646346259425604 | 211,563,874.643353 | 211,709,381.797118 |

Source transaction:
https://basescan.org/tx/0x7b8e692e523eca62aaad95e04e279ad7d8c31b38c0694af18f1a0635f54323ae

Ethereum receipts:
https://etherscan.io/tx/0xac90aa7e2dc4035c921324e3a10ad3ba6c39f21826375b1558ee6acc27964a22
https://etherscan.io/tx/0xb2c9215da24ae539d7a3fe9b65b731d079f0aaf0229e51e885e35d319e05c9e8

The canonical fixture proves each link independently of amount/date proximity:

- Base `MessagePassed` embeds the complete relay payload, including both bridge
  contracts, token pair, source ALM, recipient ALM, and actual raw token amount.
- Hashing that payload reproduces Ethereum's successful `RelayedMessage`.
- Re-encoding the six withdrawal arguments reproduces the portal withdrawal
  hash; the matching `WithdrawalFinalized` explicitly reports success.
- Actual source burns and escrow-to-ALM token payments match those units.

The exact historical adapter creates two funded in-flight claims and releases
each only at its authenticated receipt. sUSDS's value grows in transit but its
borrowed basis does not. These claims get separate tracing-only financing rows;
they are not reported revenue allocations and receive no idle-USDS exemption.
A source-only cutoff retains the pending claims. A receipt without the observed
source cannot create opening funding. Unknown source funding remains uncertain.

Tests cover independent message/withdrawal hashes and cash legs, one-week
custody, appreciation without new basis, partial cutoffs, invalid receipts,
and idempotence. The integration hook is exercised with existing spell/financing
tests. Full Spark replay is running; no improvement in eligible monthly costs
is claimed before its result is available. Published reports/API are unchanged.

Follow-up: the Optimism/Unichain withdrawal tests exposed funding-ratio mixing
when two identifiable bridge legs shared one transaction clearing account.
The Base source is now split into one replay batch per authenticated token leg.
An unequal-funding regression verifies Sky-funded USDS stays distinct from
wholly earned sUSDS, including uncertainty propagation. See
`docs/spark/op-unichain-withdrawals-2026-10-08.md`.

## June 22 withdrawal: one delivered leg and one pending leg

The smaller withdrawal before the July spell is now also covered, without combining
its independently funded USDS and sUSDS legs:

- Base source: [0x133b60…0e64](https://basescan.org/tx/0x133b60b42bdf84271efc2d743808e66c0b4796eadd19ab2e34de9da9da910e64),
  June 22, block 47,673,994. Burns exactly 10,000 USDS and 10,000 sUSDS from
  Spark's ALM, with two canonical MessagePassed payloads naming Ethereum ALM
  `0x1601843c5e9bc251a3272907010afa41fa18347e` as recipient.
- sUSDS arrival: [0x67a82b…db1c](https://etherscan.io/tx/0x67a82b0fafa4e6ccd659b10c90feba4060a1611cd5c6f5a20e2d5edf9a2bdb1c),
  June 29, block 25,424,363. The relayed message and finalized withdrawal hashes
  authenticate delivery of those same 10,000 sUSDS. Its value changes from
  $11,006.22276111643838 to $11,013.77116017150251 while in flight; this gain
  creates no borrowed basis.
- The separate 10,000 USDS message has no successful relay through the August
  pin. Its authenticated burn becomes a pending claim, not a loss, income, or
  fabricated Ethereum payment. The portal's pinned `finalizedWithdrawals`
  mapping is checked independently for both message hashes in the fixture.

This removes the source's $21,006.22276111643838 unexplained outflow and the
$11,013.77116017150251 unexplained receipt. It leaves the 10,000 USDS claim
outstanding and excluded from ALM idle cash. Actual borrowed funding remains
whatever the preceding capital history proves. The evidence is in
`tests/fixtures/spark_base_june_withdrawals.json`; regression tests recompute
both message hashes and test the partial-cutoff and separate-funding behavior.
