# Address inventory for Prime wallet monitoring

Sky's **Monthly Settlement Cycle (MSC)** calculates amounts owed between Sky and
its **Prime Agents** (investment operators such as Spark and Grove). Each operator
uses **ALM proxies**: custody wallets/contracts that hold assets and move funds to
investment destinations, called **venues** in configuration.

[`msc_addresses.csv`](msc_addresses.csv) gives a monitoring bot a labeled address
reference for those wallets, tokens and counterparties. It helps flag transfers
that the configured accounting does not explain. It does not run a bot or book
transactions.

**One row is one config reference, not one unique wallet.** An address may have
multiple rows because it serves several roles or venues. Always match the chain
as well as the address. The file includes all six configured Primes; some have
no configured ALM wallet.

## Generate or verify

From the repository root, with Python 3.11+ and this project's dependencies installed:

```sh
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python scripts/export_msc_addresses.py
.venv/bin/python scripts/export_msc_addresses.py --check
```

No blockchain connection, database or credentials are needed. The unit suite
checks the committed CSV against config: regenerate it in the same PR when
relevant config changes. An empty or missing config directory fails without
replacing the existing export.

## Using the file

1. Select `watch_alm=true`; deduplicate `(prime, chain_id, address)` to obtain the
   configured ALM watchlist, including Grove's additional investment custody proxy (Diamond PAU).
   Keel and Skybase currently configure only SubProxies (treasury/governance wallets): they have rows, but no
   ALM watch target. Do not silently scan those SubProxies as ALMs.
2. Scan **all tokens** and both transfer directions at those wallets. Match token
   contracts and counterparties separately against `(prime, chain_id, address)`.
   Shared addresses deliberately retain multiple rows and venue associations.
3. Use `venue_id`, `role`, `source_file` and `source_path` to find the relevant
   accounting rule. Flag unknown tokens/counterparties, reporting-only matches,
   ambiguous matches and transfers without a supported classification.

An address match is **not proof that a transfer is booked**, that it is income,
or that it is authorized. Known USDC alone does not explain a transfer to a new
venue. A payer can return principal as well as interest. The CSV is a discovery
and labeling input, not a transaction allowlist or a completed monthly ALM review.

For example, a USDC transfer out of Spark's ALM can match the known USDC token
while its destination is unknown. Flag that destination; do not mark the transfer
as accounted for merely because USDC appears in the CSV. For each observed transfer,
keep chain, transaction hash and log index so retries do not duplicate alerts.

## Columns

| Columns | Meaning |
|---|---|
| `prime`, `chain`, `chain_id`, `address` | Chain-specific identity; lowercase EVM address |
| `role`, `label` | Config-derived function and readable label, not verified contract type |
| `venue_id`, `venue_label` | Owning venue; blank for prime-wide rules |
| `msc_enabled`, `reporting_enabled`, `settlement_method` | Parent venue settings: MSC settlement versus economic reporting; prime-wide rows have enabled=true. These are not per-transfer booking verdicts |
| `watch_alm` | Explicit ALM custody references only; not tokens, SubProxies or arbitrary holder overrides |
| `skip_position` | Configured `skip` flag; cash-distribution rules may still apply |
| `start_date`, `end_date` | Configured scope bounds, inherited from parent; blank means unspecified, not independently verified inception/closure |
| `source_file`, `source_path`, `chain_basis` | Reproducible origin and chain resolution |
| `is_zero_address` | Mint/burn/native-token or empty-hook sentinel; never an ALM watch target |

Historical and reporting-only references remain in the file. Apply date bounds
when interpreting activity, but do not suppress alerts for unexpected transfers
outside those bounds. Oracle and receipt-source chain overrides take precedence
over the venue chain. Unknown address roles remain `configured_address`; unresolved
chains fail generation rather than defaulting to Ethereum.

## Role guide

- `alm_proxy`: investment custody wallet to watch; `subproxy`: separate treasury/governance wallet.
- `position_holder`: another configured asset holder, which may be a vault or escrow rather than an ALM.
- `venue_token` / `underlying_token`: a position's configured asset or base asset. Some venue addresses represent custodians, not ERC-20 contracts.
- Payment sources, recipients and redemption roles: addresses used for income or returning investment capital. Their full accounting rules remain in config.
- `psm`: stablecoin conversion facility; `basin`: investment funding contract; `oracle`: valuation source; `implementation`: contract code reference. These are not automatically custody wallets.
- `configured_address`: address retained without a specialized role; investigate before classification.

## Coverage and limits

Reads all top-level `config/*.yaml` Prime definitions (`id` + `addresses`), plus
supplementary `venues` entries linked by `prime` and `venue_id` (currently issuer
and redemption settlement configs). Includes structured address values and keys:
ALMs, SubProxies, alternate holders, venue/underlying tokens, pools, PSMs, basins,
oracles, payers, receipt recipients, redemption managers and implementations.
`external_alm_sources` activation dates are retained. Principal-return override
addresses are references to the full rules, not automatically classified transfers.

This is **config coverage**, not an exhaustive on-chain address registry. It does
not extract comments/prose, transaction hashes, pool IDs, tracing-only JSON,
unrelated treasury-management (TMF) and non-MSC contracts, or addresses hardcoded elsewhere in the codebase. Mainnet
PSM routing contracts, dynamic redemption managers/pockets and unconfigured
custodians may therefore appear as unknown and require enrichment. Do not mark
an ALM scan complete merely because every address matches this file. Alternate
`position_holder` rows identify additional custody to investigate, not necessarily
ALM proxies. No historical reports, accounting rules or monitoring services are
changed by this export.
