# Daily settlement asset controls

Canonical `write_settlement` now requires independent evidence under
`.msc-transfer-reviews/<prime>/<YYYY-MM>/asset-controls.json`. Missing, changed,
incomplete or inconsistent evidence stops the writer **before any artifacts or
output directory are created**. `--allow-overwrite` does not bypass controls.
Provisional revenue previews remain available. No historical settlement is
rewritten and no live month receives a passing review from this change alone.

## What is checked

- Every opening close and every UTC daily close is present, in order. Every
  owned account and allocator ilk has an explicit observation, including zeros.
- Each of the seven daily borrowing-base deductions matches independently
  observed eligible holdings. The eligible portions of one asset cannot sum to
  more than its value; the portfolio total has its own one-cent limit. Negative,
  missing and non-finite holdings fail. The saved `utilized` borrowing principal
  must equal observed debt minus all seven deductions. A legitimate negative
  principal is retained; the existing charging formula handles its zero floor.
- ALM USDS is checked against direct archive `balanceOf` evidence, separate from
  the event-derived balance reconstruction used by the financial calculation.
  Chain identity, finality, linked UTC boundary headers, token, owner and pinned
  block must match. Eligibility also respects configured ALM activation dates. Empty provider responses never become zero.
- Native-USDS bridge claims are reconstructed from retained complete gateway
  query responses using the existing `collect_transfers` / `daily_exposure`
  mechanism. A claim belongs to its source chain and ends on the delivery day.
  Omitting a route/history or retaining a delivered claim fails the check.
- Assets are summed once per economic claim. A vault receipt and its underlying
  look-through must share the same claim identity; they cannot be two assets.
  Lending/AMM idle portions belong inside their receipt asset's deductions.
- Daily total assets and daily total debt independently roll forward through a
  reviewed journal. Bridge sends/releases and vault deposits/withdrawals are
  internal movements, not income or expenses. Borrowing adds assets and debt;
  repayments reduce both. Income, expenses, contributions, distributions,
  valuation changes and noncash debt changes must have their own evidence.
  There is no residual/unknown/balancing-plug journal category.

A one-cent absolute tolerance applies to daily USD comparisons. Opening assets
need not equal Sky debt: contributed capital and retained earnings can exist.
The check never invents an equity balance to make the numbers agree. The
provenance retains daily asset/debt/deduction totals and the packet, review,
configuration, inventory and source hashes for audit.

Transfer-scan coverage is required at active configured ALM boundaries only.
SubProxy and other asset-holder observations remain part of the financial
inventory, but their full transfer histories are not an ALM-scan prerequisite.
Additional supplied scans must still be complete and backed by evidence.

## Evidence preparation

Follow [the full ALM review](monthly-alm-transfer-review.md) first. Resolve all
unknown tokens, payers and destinations. The review attests the completeness
and meaning of the inventory and source classifications; arithmetic alone
cannot establish ownership or discover an omitted off-chain claim.

Capture native-USDS wallets and supported bridge histories independently:

```sh
python scripts/capture_settlement_wallets.py --prime spark \
  --pins /path/to/daily-pins.json --month 2026-07 \
  --output .msc-transfer-reviews/spark/2026-07/wallets.json \
  --bridge-output .msc-transfer-reviews/spark/2026-07/gateways.json
```

`daily-pins.json` maps UTC dates to `{chain: block_number}`; include the previous
month's final close and every day of this month. For a month containing zero pre-genesis pins, also pass
`--genesis-output /path/to/genesis.json`. The script uses configured RPC
endpoints and `.env`, refuses existing output files, and issues **no approval**.
Archive calls use the canonical block hash. Retain the captured files unchanged.

This producer covers native-USDS wallets and the existing canonical-USDS bridge
routes. It does **not** claim complete automatic portfolio coverage. Other
position valuations/deduction observations and the journal must be prepared from
independent protocol reads, reviewed transfer classifications and supported
custodian statements. Existing allocation-capital and allocation-financing
capture tools can supply supporting evidence, but their funding analytics and
the permissive snapshot's fallback zeros are not a substitute for a complete
asset inventory. Missing support is a blocker, not a zero-dollar observation.
Do not calculate generic daily valuation or income as the unexplained residual.

## Retained file contract (schema 1)

`asset-controls.json` contains:

| Field | Content |
| --- | --- |
| `schema`, `prime`, `month` | `1`, prime id, `YYYY-MM` |
| `config_sha256` | SHA-256 of the current `config/<prime>.yaml` |
| `inventory.accounts` | Map of account id to `chain`, `holder`, `custody_id`, `claim_id`, `deduction_fields` |
| `inventory.ilks` | Every configured allocator ilk as lowercase `0x` bytes32 |
| `inventory.wallets` | All active ALM/SubProxy and other reviewed holder identities as `chain:lowercase-address` |
| `inventory.chains` | Every active configured settlement chain |
| `inventory.venues` | Every configured venue id to `{accounts: [ids]}` or an explicit exclusion |
| `observations`, `journal`, `bridge_evidence` | Relative JSON filenames |
| `evidence_sha256` | Map of **all** observation, journal, gateway and raw evidence filenames to SHA-256 |

Accounts represent whole owned claims. `custody_id` identifies a token, NFT
position or receivable; for ALM USDS it is the canonical native-USDS token
address. `claim_id` identifies the economic right independently of its physical
representation. Do not assign different claim ids to a receipt and its backing.
Multiple venue display recipes can reference the same account id. Exclusions
use `accounts: []` and `exclusion: {kind, rationale, evidence: [raw filenames]}`;
allowed kinds are `not_owned`, `outside_period`, `outside_msc`. Failure to read a
position is not an exclusion. Newly discovered holdings need accounts even if
no current venue recipe represents them.

Observations are an ordered JSON list, opening close first. Financial daily rows
must explicitly contain all seven deductions, `cum_debt` and `utilized`, even
when they are zero. Direct wallet accounts allow only `alm_usds`; a different
exemption cannot bypass the configured ALM activation date. Arithmetic uses an
isolated Decimal context and does not inherit caller rounding or traps.

Block pins may repeat on a halted chain. A zero pin requires an observation's
`pre_genesis: {chain: "genesis.json"}` reference to a retained, hash-bound output
from `--genesis-output`. Its chain identity and linked genesis/first-block
headers must prove the first block occurred after that UTC close. Holdings and
deductions on the chain must be zero and cite that proof. Once the chain is live,
its positions need actual balance evidence; a genesis proof cannot stand in for
a live balance. Missing or unsupported genesis evidence blocks the report.

Observation shape:

```json
{
  "day": "2026-07-01",
  "pins": {"ethereum": 123},
  "assets": {
    "account-id": {
      "block": 123, "value_usd": "90",
      "deductions": {"alm_usds": "90"}, "evidence": ["wallets.json"]
    }
  },
  "debt": {
    "0x-actual-ilk-bytes32": {
      "block": 123, "value_usd": "100", "evidence": ["raw-vat.json"]
    }
  }
}
```

The fragment illustrates shape only, not a real passing review. Each asset must
explicitly supply exactly its configured `deduction_fields` (empty for an asset
with no exemption). Allowed fields: `alm_usds`, `psm_usds`, `sde_av`, `curve_idle`,
`lending_idle`, `basin_idle`, `bridge_usds`. All amounts are non-negative decimal
strings or integers. Every reference must resolve to a hash-bound raw artifact;
observations/journal cannot reference themselves as their own source evidence.

The journal is an ordered list `{day, entries: [...]}` for **every** settlement
day, including explicit empty entry lists. Each entry has a globally unique
transaction/log/leg `id`, `kind`, non-negative `amount_usd`, `rationale` and
`evidence: [raw filenames]`. Kinds are `borrowing`, `repayment`, `contribution`,
`distribution`, `income`, `expense`, `valuation_gain`, `valuation_loss`,
`debt_accrual`, `debt_forgiveness`, `internal`. Classify actual events and marks;
do not choose an amount just to close a residual.

Extend `review.json` with `prime`, `month`, `config_sha256`, reviewer identity,
`reviewed_at` with timezone, `status: "reviewed"`, `unclassified_count: 0`, the
same `evidence_sha256` map, and `asset_inventory_sha256` (SHA-256 of sorted,
compact UTF-8 JSON for `inventory`). `scan_coverage` must map every reviewed
wallet identity to `status: "complete"`, `unclassified_count: 0`, `transfer_count`,
equal `classified_transfer_count`, `from_block`, `to_block`,
`token_standards: ["erc20", "erc721", "native"]`, and `evidence: [raw filenames]`.
Ranges must cover the entire month at the settlement's opening/closing pins.
A filename, zero count or zero residual alone never authorizes these claims.

Check an existing report without modifying it:

```sh
python scripts/check_settlement_assets.py \
  --provenance /path/to/provenance.json \
  --controls .msc-transfer-reviews/spark/2026-07/asset-controls.json
```

For a new canonical report the writer performs the same check automatically.
No passing production packet is shipped with this PR. Completing real source
coverage and human review is required before the next canonical write.
