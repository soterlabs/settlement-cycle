# Grove September 2026: missing venue and proxy transfer audit

PR #226 was squash-merged as `ca28e58474b368bc17386c71777db8deb0876d4d`.
This separate change adds two venue/holder pairs using existing mechanisms:

- **E43:** Base Grove x Steakhouse USDC, `0xbeef0786756810478b88982de00f3cd7fdb8e7c7`,
  held by the Base ALM `0x9b746dbc5269e1df6e4193bcb441c0fbbf1cecee`.
  ERC-4626 category B, 18-decimal shares, 6-decimal Base USDC underlying.
- **E44:** Ethereum AUSD/USDC Uniswap V3 pool,
  `0xbafead7c60ea473758ed6c6021505e8bbd7e8e5d`, held by Diamond PAU ALM
  `0x0dcd9298e163dfd3c0b5b00f0d9093c36e40a153`.
  Category F with the existing NFPM discovery, liquidity-event and fee valuation.

The pool was already enabled as E12 for the legacy ALM and E30 for the alternate
holder. Neither row covers the Diamond PAU holder. E44 is a distinct position,
not a duplicate of either existing row. NFT IDs are discovered, not configured.

## Measured September omissions

These figures compare the on-chain evidence with the checked-in September
`summary.md`. That report has neither E43 nor E44. Principal is not revenue.

| Venue | Opening value | Closing value | Capital under existing method | September revenue |
|---|---:|---:|---:|---:|
| E43 Base vault | $0.00 | $45,007,605.86 | $45,000,386.37 | **$7,219.49** |
| E44 Diamond pool | $1,022,418.74 | $20,002,378.41 | $18,978,957.12 | **$1,002.55** |
| Additional venue revenue | | | | **$8,222.04** |

The $8,222.04 is a venue-revenue measurement before rerunning the complete
settlement and CoF allocation. It is not an approved settlement credit. The
published summary and workbook are unchanged by this PR. E44 already held an
opening position: this September audit does not determine any earlier omission.

The Base vault received $20m on September 28, $20m on September 29 and $5m on
September 30, with no withdrawal. Closing value minus actual cash is $7,605.86.
The existing category B method prices each day's net share flow at end-of-day
NAV; it treats $386.37 as capital, producing $7,219.49 in revenue. This PR retains
that existing convention, also used for E19 and E23; it does not introduce a new
cash-based methodology. Complete share-transfer deltas equal the pinned wallet
balance changes for all three Base vaults.

Base deposit transactions:

- [September 28, $20m](https://basescan.org/tx/0xb7ecfefb5a9fda1939ec663ea8717e51738bb220671cdd87ffe8dd24cf438c91)
- [September 29, $20m](https://basescan.org/tx/0x77f37c6d98e4760819954b903b12ad188c9093fbb6026314281cf5e893767c2e)
- [September 30, $5m](https://basescan.org/tx/0xe7c6c3267b05a682752dd93b23e59c2400b59b3d6b4bb9dfd8e9bcfac1ae7376)

The first two are reallocations from E23. E44 holds NFT 1352494 at both month
boundaries. Its seven liquidity increases total $18,978,957.122864, with no
September decrease or collection. Production V3 valuation, including accrued
fees, produces the opening and closing amounts above.

## Existing bookings independently reproduced

| Item | On-chain September amount | Existing report |
|---|---:|---|
| E19 Base High Yield, category B revenue | $281,614.44 | Matches |
| E23 Base Prime Instant, category B revenue | $710,966.35 | Matches |
| E12 legacy Uniswap pool revenue | $12,817.36 | Matches |
| E12 fee-only collections, included in the row's capital outflows | $17,544.11 | Included, not extra revenue |
| E21 cash distribution | $172,167.34 | Matches |
| E38 two incentive payments | $665,172.00 | Matches |
| E42 warehouse cash distribution | $1,658,948.54 | Matches |

Cash distribution references:

- [E21](https://etherscan.io/tx/0x41affed98b59cbf96df4fbac0b91ccd39f4c4e8442a0115387943e105d73fc7c)
- E38: [payment 1](https://etherscan.io/tx/0xd59c1a87263b7c56f55d1c31e34b446e6aab12e8fd5398dbae3e63f2b4302ceb),
  [payment 2](https://etherscan.io/tx/0xfc5606d1cb53de3f0c430559ff0c3021edf4e7e47d7d9588074af2f568312a83)
- [E42](https://etherscan.io/tx/0xd0182579a1a51087ca7a2a12b889a5e4fc0a49581f9f5fab5bbcdaaafa5d30e0)

## Warehouse principal: follow-up implemented

The Ethereum proxy sent **$71,250,000** to the configured E42 principal relay
`0x3e23311f9ff660e3c3d87e4b7c207b3c3d7e04f0` in September:

| Date | Cash sent | Transaction |
|---|---:|---|
| September 10 | $1,000 | [transaction](https://etherscan.io/tx/0x1524b24325d57278986f750a9b6afc8761403ea8f0d1279d6bf7819c4bb7f50c) |
| September 11 | $23,749,000 | [transaction](https://etherscan.io/tx/0xdea5827436070521fb43e7934ae2c7f092892d740da8d09faf2b68f5047d9ac4) |
| September 12 | $47,500,000 | [transaction](https://etherscan.io/tx/0x5ff48b631b5c53a8df2c854de6c6422404efd9cf404cbc52a79653aa3ed44bcb) |

The original audit found that E42's `notional_principal_usd` schedule ended at
$304,000,001 on July 27, with no September funding steps. The follow-up now
adds the three dated increases above through the existing notional mechanism:
$304,001,001 on September 10; $327,750,001 on September 11; $375,250,001 on
September 12. September's end-of-day average is **$349,916,701**. The extra $1
from the original July test remains part of principal. August is unchanged.

The offline audit now verifies those steps against the actual transfers and
pinned block dates. This repairs per-venue CoF attribution; it does not book
principal as revenue/expense or reduce the aggregate borrowing charge already
calculated on Grove's debt. Published September artifacts still require a
separate reconciliation. See [remaining-item implementation](remaining-reconciliation-items.md).

## Scope and reproducibility

The scan covers September 1 inclusive through October 1 exclusive, using
`(opening block, closing block]`:

| Chain | Opening block | Closing block | Completed 1,000-block chunks |
|---|---:|---:|---:|
| Ethereum | 25,878,704 | 26,093,737 | 216 |
| Base | 50,715,726 | 52,011,726 | 1,297 |

Both boundary headers are timestamped at 23:59:59 UTC before the respective
month boundary. Bidirectional Transfer queries were deduplicated by chain,
transaction hash and log index. No query failures or pagination gaps were
accepted. Public Tenderly gateways supplied the log scan; public archive RPC
calls supplied pinned balances, quotes and V3 state. Ethereum Blockscout's
independent transfer inventory was cross-checked against transaction receipts
and the RPC scan: legacy ALM 745 logs/241 transactions; Diamond PAU 79/20;
alternate holder 0/0. Base ALM: 61 logs/37 transactions.

Four Ethereum receipts involve unvalued GLAMDOG/ALPHA tokens outside the
financial venue inventory. They are retained in the evidence, not recognized
as dollar revenue. Swaps, mints/burns, bridges, funding, redemptions and LP
principal movements must not be summed as income just because cash enters
or leaves the proxy.

This is **not a blanket all-chain booking certificate**. Native/internal
transfers, Avalanche, Plume, Monad, off-chain statements, bridge message matching
and every historical capital classifier are outside this audit's verification.
The matched rows above establish their listed amounts, not that every other
flow was correctly classified. The published September artifacts predate the missing-venue and warehouse
corrections; the tracking fixes do not themselves update those artifacts.

The checked-in [evidence archive](evidence/september-2026-venue-audit.json)
contains the complete scoped transfer inventory, NFPM receipt events, pinned
V3 RPC inputs/outputs, vault boundary states and event-day NAVs. Replay without
RPC credentials or network access:

```sh
python scripts/audit_grove_september_2026.py
```

The replay bypasses both local and Postgres caches, including when run from a
shell configured for a live settlement worker. Missing pinned RPC evidence
fails without a network fallback. It validates the period, holders, event
identities, NFPM emitter, event encoding and collected record/chunk counts
before calculating amounts, and rejects conflicting RPC responses. Collection
counts detect a damaged or partial archive; they do not independently prove
on-chain completeness or broaden the scope described above.

Fresh settlement generation with E43/E44 requires live configured sources and
archive RPC access; old fixture captures do not acquire the new venues merely
because the YAML changed:

```sh
python scripts/run_live_2026.py --primes grove --months 2026-09
```

Review that regenerated result, including the updated notional schedule, before
publishing revised figures or a reconciliation. This PR itself publishes no
replacement settlement.
