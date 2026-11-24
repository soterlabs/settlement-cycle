# MSC address inventory

[`msc_addresses.csv`](msc_addresses.csv) is the flat address reference for an
ALM inflow/outflow monitoring bot. Regenerate from the repository root with the
project's installed Python environment:

```sh
python scripts/export_msc_addresses.py
python scripts/export_msc_addresses.py --check
```

No RPC, database access, settlement replay or credentials are required. The unit
suite checks the committed CSV against configuration, so relevant config changes
must regenerate it in the same PR.

## Using the file

1. Select `watch_alm=true`; deduplicate `(prime, chain_id, address)` to obtain the
   configured ALM watchlist, including Grove's additional Diamond PAU holder.
   Keel and Skybase currently configure only SubProxies: they have rows, but no
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

## Columns

| Columns | Meaning |
|---|---|
| `prime`, `chain`, `chain_id`, `address` | Chain-specific identity; lowercase EVM address |
| `role`, `label` | Config-derived function and readable label, not verified contract type |
| `venue_id`, `venue_label` | Owning venue; blank for prime-wide rules |
| `msc_enabled`, `reporting_enabled`, `settlement_method` | Parent venue settings using the settlement loader's defaults/legacy flags; prime-wide rows have enabled=true |
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
non-MSC/TMF contracts, or addresses hardcoded elsewhere in the codebase. Mainnet
PSM routing contracts, dynamic redemption managers/pockets and unconfigured
custodians may therefore appear as unknown and require enrichment. Do not mark
an ALM scan complete merely because every address matches this file. Alternate
`position_holder` rows identify additional custody to investigate, not necessarily
ALM proxies. No historical reports, accounting rules or monitoring services are
changed by this export.
