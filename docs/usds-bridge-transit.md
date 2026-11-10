# Native USDS bridge transit and borrowing costs

Native USDS held by an ALM is excluded from the Base Rate principal. Moving that
USDS through a canonical bridge must preserve this treatment until delivery.
This also covers USDS withdrawn from a PSM3 or AMM before bridging: once withdrawn,
it is the same raw USDS asset. It does not exempt USDC, sUSDS or LP tokens.

The [Atlas A.2.4.2.4 rule](https://sky-atlas.io/#12778509-d4b4-4e45-b80f-11a3b7ecbad9)
attributes an asset to its source chain until release/minting, with destination
attribution starting on the release date. At each daily close the new
`bridge_usds` deduction retains the source claim on `[send date, release date)`.
The release date uses existing destination holdings/positions; there is no extra
bridge deduction on that date. Same-day completed transfers add no daily claim.
Open claims survive month and year boundaries. Vat debt remains unchanged.

## Implementation and scope

- `src/settle/normalize/usds_bridge_transit.py` reads pinned, complete canonical
  gateway histories using the existing HyperSync persistence mechanism. It
  authenticates native token, gateway pair, sender and recipient. Only the same
  prime's configured ALM on each chain qualifies. No manual windows are needed.
- Canonical Sky USDS bridges between Ethereum and Arbitrum, Base, Optimism and
  Unichain are supported, in both directions. Unconfigured chains, other bridges,
  EOAs and other recipients are outside this implementation and audit scope.
- Gateway contracts guard finalization with their authenticated counterpart.
  Full fungible payloads (route, direction, owners, amount and OP extraData) are
  matched as an ordered multiset. Reordering indistinguishable identical claims
  cannot change aggregate pending USDS. There is no nearest-date matching.
- Failed/incomplete reads, orphan owned deliveries, conflicting event identities
  and invalid amounts/timestamps fail the calculation rather than assume zero.
- `compute/monthly_pnl.py` passes the separate daily deduction into the existing
  `compute/sky_revenue.py` formula. Rates, subsidy caps and the zero charge floor
  are preserved. Retained revenue results, provenance and the workbook Debt tab
  include the deduction; monthly consolidation checks it against retained claims,
  including configured owners/routes, unique event identities and chain pins.
  Old snapshots decode with an empty claim list; existing code/config version
  checks still require a compatible revision before monthly reuse.

Gateway deployments are from the [Spark address registry](https://github.com/sparkdotfi/spark-address-registry).
Event/authorization definitions are in the [Sky OP bridge](https://github.com/sky-ecosystem/op-token-bridge)
and [Sky Arbitrum bridge](https://github.com/sky-ecosystem/arbitrum-token-bridge).

## Read-only 2026 audit

The scan covers January 1 through November 8, 2026 (completed daily closes),
using all six prime configurations and four canonical routes. It found seven
Spark transfers and no matching transfers for Grove, Obex, Osero, Keel or Skybase
within that scope. It does not certify arbitrary bridges or unconfigured wallets.

Grove did bridge funds back: the existing authenticated CCTP v2 evidence contains
**15 Base → Ethereum messages totaling 34,008,407 USDC** through August (and
62 Ethereum → Base messages totaling 314,595,379.874274 USDC). For example,
the [March 5 source transfer](https://basescan.org/tx/0x3d6bd9d1901198728fcf4c679ae3582783ceeb40cf0d78a21da69e28aa437e8b)
bridged 9 million USDC. These are USDC movements, which do not qualify for the
native idle-USDS exemption. The counts are retained historical evidence, not
an exhaustive new scan of all Grove bridges through November. See
`docs/grove/cctp-v2-2026-10-08.md` and `tests/fixtures/grove_cctp_v2_proofs.json`.
"No native-USDS matches" does not mean "no bridge activity" or establish that
there are no other bridge-related accounting issues.

All dates below are UTC, in 2026. The three small June withdrawals remain claims
through their actual release, even when the challenge period has elapsed.

| Route | USDS | Sent | Released | Evidence |
| --- | ---: | --- | --- | --- |
| base → Ethereum | 10,000.00 | Jun 22 14:08:55 | Nov 03 12:30:23 | [Source](https://basescan.org/tx/0x133b60b42bdf84271efc2d743808e66c0b4796eadd19ab2e34de9da9da910e64) / [release](https://etherscan.io/tx/0xa2fe7d047d7e6a1ab47c95ac7931faa4214a31bea0bb3db72fd79d50f2438cfb) |
| unichain → Ethereum | 10,000.00 | Jun 22 14:09:02 | Pending at Nov 8 | [Source](https://uniscan.xyz/tx/0x6ba2c8158c8b1bc1e9daeafc5903f0d46978ccbb37d92eb80fea931582236625) |
| optimism → Ethereum | 10,000.00 | Jun 22 14:09:11 | Pending at Nov 8 | [Source](https://optimistic.etherscan.io/tx/0x8f10a77f52f7c583a8606120736ddd5766f6cb24027808d8f0238d856ca21d77) |
| base → Ethereum | 146,550,618.21 | Jul 06 18:05:53 | Jul 13 20:22:35 | [Source](https://basescan.org/tx/0x7b8e692e523eca62aaad95e04e279ad7d8c31b38c0694af18f1a0635f54323ae) / [release](https://etherscan.io/tx/0xac90aa7e2dc4035c921324e3a10ad3ba6c39f21826375b1558ee6acc27964a22) |
| unichain → Ethereum | 99,990,828.81 | Jul 06 18:05:59 | Jul 13 20:25:11 | [Source](https://uniscan.xyz/tx/0x33f66feba68e407fd92e5b4c46da5a53389096a5a6b9c40b8909f1a57986edda) / [release](https://etherscan.io/tx/0xce77f64f1620b2c58bec71b05f5805b6ac3c2d4674ca808b0ec12fdda4a29907) |
| optimism → Ethereum | 100,304,256.59 | Jul 06 18:06:05 | Jul 13 20:23:59 | [Source](https://optimistic.etherscan.io/tx/0x216a9de9b07a0c954dd797b6abf95c4ed0af6b1ba0f5156d0c40533fb656a16e) / [release](https://etherscan.io/tx/0x32c5a7912f97abe46a7c384f938c764a3719a9d20d90d063f765e4f2df05fe4a) |
| arbitrum → Ethereum | 99,326,272.78 | Oct 12 14:15:11 | Oct 19 18:39:59 | [Source](https://arbiscan.io/tx/0x19f7159dc1ddd429ea6c143dfe7ef7665431696d460f153e4f9c9a5ffdb5aeba) / [release](https://etherscan.io/tx/0x2b7241789acd29f18928f2f11faba2784ea5544e2683b5e5cec22d0599940c1a) |
The former wallet-only deduction dropped these claims during transit. Frozen
monthly Debt tabs and retained October daily revenue reproduce a positive
borrowing charge on the affected dates. Removing only the omitted USDS claim,
while preserving each original daily rate convention and subsidy cap, gives:

| Month | Excess calculated borrowing costs (USDS) |
| --- | ---: |
| January–May | 0.00 |
| June | 28.42 |
| July | 255,655.55 |
| August | 93.37 |
| September | 92.14 |
| **January–September total** | **255,869.48** |
| **October** | **75,063.88** |

The January–September total is a historical reconciliation candidate. October
belongs to the October MSC calculation. November 1–8 still has pending claims;
no saved daily charge was available in this audit, so its impact is **unmeasured**.
Totals use unrounded daily values before rounding to cents.

These are calculated excess charges, **not verified settlement payments or
approved net credits**. This PR books no adjustment, publishes no API revision,
and changes no previously published settlement. Before booking, compare prior
reconciliation credits and actual settlement execution to prevent another credit
for amounts already corrected. This audit isolates bridge treatment; it does not
restate historical rates or other policy changes.

## Evidence and reproduction

- `reconciliation/usds-bridge-2026/audit.json`: exact amounts, timestamps, source
  and destination hashes, chain pins, gateway coverage witnesses, source/config
  hashes, original daily charges and measured differences.
- `reconciliation/usds-bridge-2026/spark-october-retained-daily.json`: October
  daily financial inputs with retained database revision/result identities.
  Only read-only access was used; the database was not updated.
- `tests/fixtures/usds_bridge/spark_2026_gateway_logs.json`: authenticated gateway
  events for all seven transfers, replayed without network by the tests. This
  filtered fixture proves decoding/matching; complete scan coverage is recorded
  separately in the audit's per-gateway witnesses.
- June–September workbook baselines are unchanged in `settlements/spark/2026-*`;
  their SHA256 hashes are recorded in the audit. The replay fits nominal,
  effective-annual or monthly-equivalent daily factors to the original charge,
  accepts only consistent solutions, and marks unresolved days as unknown.

Repeat the live read-only scan with an optional indexer credential:

```sh
python scripts/audit_usds_bridge_transit_2026.py \
  --through 2026-11-08 --cache /tmp/usds-bridge-2026-cache \
  --env-file /path/to/local.env --output /tmp/usds-bridge-2026-audit.json
```

`--env-file` reads only `ENVIO_API_TOKEN`. To include October, put the retained
October input file above in the cache as `spark-october-retained-daily.json`.
Without it the script explicitly marks missing October charges as unknown.
The raw response cache is keyed by chain, gateway and pin. Archive coverage and
pagination failures are errors. Synthetic tests and this read-only comparison
are not a completed monthly all-token ALM transfer review; the normal review
requirement still applies before a new monthly settlement calculation.
