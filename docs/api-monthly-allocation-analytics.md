# Monthly allocation borrowing costs and yields

```http
GET /v1/revenue/{prime}/monthly/{YYYY-MM}/allocations
GET /v1/revenue/{prime}/monthly/{YYYY-MM}/allocations?venue=S18
GET /v1/revenue/{prime}/monthly/{YYYY-MM}/allocations?revision=<64-hex revision>
```

This is a new, separate monthly endpoint. Daily revenue endpoints and published
settlements are unchanged. Requests read PostgreSQL only: no RPC, indexer,
revenue calculation or capital replay runs on an HTTP request.

## Response contract

The document contains `schema_version`, `prime`, `month`, `cadence: "monthly"`,
the complete calendar `period`, `revision_id`, `published_at`, `source_hashes`,
`provisional: true`, calculation policies and an `allocations` array.

| Allocation field | Meaning |
|---|---|
| `venue_id`, `label` | Allocation identity |
| `average_original_principal_usd` | Daily average original principal, excluding identified gains; may still contain unresolved Savings funding |
| `closing_original_principal_usd` | Month-end original principal |
| `average_yield_exposure_usd`, `yield_denominator` | Separate yield denominator: time-weighted value/notional, or outstanding loan principal for Anchorage |
| `borrowing_cost_usd` | Monthly cost on the usable Sky-funded basis, after applicable deductions; null if unavailable |
| `borrowing_cost_status` | Why the cost is available, zero, grouped elsewhere or unresolved |
| `gross_revenue_usd` | Booked investment revenue, including external revenue |
| `prime_retained_revenue_usd` | Venue revenue retained by the Prime after SDE |
| `net_pnl_usd` | Prime-retained venue revenue less allocation borrowing cost |
| `gross_monthly_return`, `net_monthly_return` | Unannualized period profit / average exposure |
| `gross_apy`, `net_apy` | `(1 + monthly_return) ** (365 / days_in_month) - 1` |
| `yield_status` | Available, gross-only, missing revenue, no positive exposure, or a grouped/non-investment row |
| `excess_deduction_credit_usd` | Excess deduction kept separate from the allocation charge for global reconciliation |
| `spread_reimbursement_excluded_from_net_usd` | Existing spread reimbursement, disclosed but not included in this per-venue net definition |
| `savings_dependency_days` | Days with a possible unresolved Savings mix, not a quantified Savings balance |
| `cost_reported_under` | Parent/boundary account; prevents charging interior holdings twice |

All numeric amounts and returns are decimal **strings**, with full precision.
Returns are ratios: `"0.05"` means 5%, not 0.05%. Consumers should format for
display without treating a rounded negative zero as no expense.

Sky-funded idle non-USDS stables pay the borrowing rate. A low charge on an
idle account can reflect a small borrowed portion rather than an exemption:
retained gains do not create borrowing. For example, August Grove E15 has
a positive borrowing cost and an annualized net yield of approximately
**-0.0042%**, which previously displayed as `-0.00%`.

## Availability and interpretation

- `savings_split_pending`: cost and net yields are null; gross yield may be
  available. Do not substitute zero or assume the entire balance is Savings.
- `calculable_under_assumption`: the operator-confirmed Sky-except-Savings
  policy supplies funding attribution; this is not a claim of complete
  transaction-by-transaction funding proof.
- `excluded_from_cost` / `no_principal_in_period`: actual zero cost, not an error.
- `covered_by_boundary`: funding is represented by the named EOA/custody
  boundary; do not add a second cost for the interior row.
- `revenue_not_in_snapshot`: a cost may be known but gross/net yields are null.
- Savings liabilities and income-only rows are not independent investments.

Net follows the existing per-allocation convention: after SDE and borrowing
costs, excluding prime-level adjustments and spread reimbursements. The net
figures do not sum directly to canonical Prime settlement PnL. These analytics
remain explicitly provisional; global debt and published charges are unchanged.

Cash-recognized income is annualized as booked. A monthly Galaxy/Anchorage/Paxos
yield is not automatically its contractual accrual rate.

## Selection, caching and errors

- Complete UTC months only; invalid/incomplete months or malformed revision
  IDs return 422.
- Unknown prime, absent monthly data, absent revision or unknown venue return
  404 with `Cache-Control: no-store`; absence is never returned as an empty
  zero-valued result.
- Storage failures return 503 without database credentials, also `no-store`.
- Successful responses use ETags and `Cache-Control: public, max-age=300`.
- Omitting `revision` selects the latest published revision for exactly that
  Prime and month. Corrections append a revision; previous ones remain readable.
- `venue` filters the selected document without changing its revision. The
  filtered document's `count` is one and its ETag reflects the filtered body.

## Publication and rollout

The initial reviewed artifacts cover **Spark and Grove, August 2026**. Other
months are unavailable until separately calculated and imported. A code deploy
alone does not imply that a dataset has been published.

Prepare an inspectable bundle without touching any database:

```sh
PYTHONPATH=src .venv/bin/python scripts/publish_monthly_allocations.py \
  --readiness reconciliation/allocation_capital_readiness_2026_08.json.gz \
  --yields reconciliation/allocation_yields_2026_08.json \
  --output /tmp/monthly-allocation-api-preview.json
```

After deploying the API code, import the reviewed artifacts using the existing
`DATABASE_URL` configuration:

```sh
PYTHONPATH=src .venv/bin/python scripts/publish_monthly_allocations.py \
  --readiness reconciliation/allocation_capital_readiness_2026_08.json.gz \
  --yields reconciliation/allocation_yields_2026_08.json \
  --publish
```

Publication checks that yield and capital/cost artifacts belong to the same
snapshot and complete month. It atomically imports both Primes into the new
`allocation_monthly_results` table, with deterministic content-addressed
revisions. An interrupted transaction does not supersede the old data;
identical retries create no new revision. No daily-revenue table or settlement
file is updated. Future monthly imports follow the same process; no new
scheduled calculation job is introduced in this change.

The schema is bootstrapped by API startup and by the importer. Actual
production availability must be verified against the deployed endpoint after
deployment and import; unit/integration test success is not that verification.
