# Configured yield was missing from capital-income classification

Capital extraction recognized `external_alm_sources` but ignored the existing
per-venue `cash_distributions` configuration. This is a real classification
bug: known cash yield was recorded as an unexplained funding receipt. In Grove,
Galaxy ARCH holds GACLO on Avalanche but pays USDC on Ethereum, so looking only
at the investment's chain also misses the receipt.

Fresh extraction now matches the exact configured **receipt chain, token,
payer and primary ALM recipient**. The investment may be on another chain.
Changing a payer, token or recipient does not inherit this income treatment.
The normalizer fingerprint automatically changes, invalidating old snapshots
for fresh extraction. Existing principal-return overrides remain respected.

The explicit historical adapter applies the same classification to the 13
reviewed Grove receipts in saved histories, allowing this investigation to
replay without re-fetching every venue. It accepts already classified receipts
idempotently and rejects changed/mixed rows. The canonical fixture is checked
against `config/grove.yaml`; unknown transfers stay unresolved.

| Existing configured yield source | Receipts | USDC |
|---|---:|---:|
| Galaxy ARCH E21, `0xac3d…f1b` | 10 | 2,630,006.67 |
| Galaxy Warehouse E42, `0xba79…e82c` | 3 | 474,489.14 |
| Total | 13 | 3,104,495.81 |

The separate ARCH principal payer `0x9dd1…8318` is not a yield source and keeps
its principal-return treatment. Income adds no borrowed basis; any later debt
repayment from earned cash retires existing basis under the ledger's policy.

August full-history replay removes all 13 false unknowns: **53 → 40 unmatched
receipts**, with 260 unmatched outflows. The eligible subtotal remains
$1,233.51 and neither ilk fully reconciles. Diagnostic GROVE-A CoF remains
bounded by $8,228.95–$11,784.74; this interval is not a validated allocation
subtotal. Published revenue and borrowing-cost controls are unchanged.

Evidence: `reconciliation/grove_cash_distributions_2026_08.json` and
`tests/fixtures/grove_cash_distribution_events.json`. Validation: 297 relevant
tests plus the additional recipient-isolation regression pass (24 tests in the
final focused normalizer/distribution run). A broad test run is also started.
