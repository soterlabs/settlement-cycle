# Spark reporting margins (v1)

Read-only projections, not new settlement charges or a restatement. Deploy this
backend before the companion reporting-dashboard Margins tab/shared-expense consumer.

## Routes

- `GET /v3/reporting/margins?prime=spark&year=2026[&cutoff=YYYY-MM-DD]`
- `GET /v3/reporting/margins?prime=spark&year=2026&revision=<selection>`
- `GET /v3/reporting/expenses?prime=spark&year=2026[&revision=<sha256>]`

Margins reuse the same reviewed/accounting statement projection as the income
statement (`accounting=true&review=true`), monthly allocation-yield publications,
and the versioned Spark expense snapshot. No HTTP read fetches chain data,
starts a replay, writes a publication, or redistributes a reconciliation residual.
Other primes/years currently return 404. Malformed selections/conflicting cutoff
and revision return 422. Store failures return 503, not fabricated zero costs.

The selection freezes the statement selection, twelve allocation revisions
(including missing slots), and expense revision. Preserve old expense JSON files
when adding a new version. Full-precision decimal amounts are retained. Historical
incomplete slots do not acquire newly published allocation data on pinned reads.
The expense hash equals the prior dashboard's ordered-JSON snapshot hash, allowing
old exports to retain the original copy. New income screens load the same backend
snapshot used by margins. Provisional grant run rates, budgets and October's 30%
reduction scenario remain estimates, not reported actuals.

## Full reporting perimeter versus covered allocation slice

`months[].numerators` exposes the exact reviewed statement investment income,
funding expense (including Savings interest), other income and computed expense
bridges. Direct expenses = distribution + other; incentives are separate;
operating includes contributor SPK compensation once. These are management
categories, not a claim to audited tax or legal-entity profit.

`months[].metrics` defines monthly yield, funding cost/asset, funding rate, NIS,
NIM, NRM before/after incentives, and NM. Full Spark ratios are populated when the daily average-balance worker has
published matching inputs for the month (see below). Missing averages must not
be replaced by closing balance-sheet stocks, original principal, or a subtotal
of only the covered venue rows.

`months[].allocation_slice` sums revenue, modeled Sky cost and average exposure
for the same positive-exposure rows, then divides summed dollars by summed average
assets. It never averages APYs. Rows with missing inputs, zero exposure, or costs
reported elsewhere are listed as excluded. Duplicate venue IDs and nonfinite or
negative capital/costs are rejected. It is partial coverage even if all legacy
rows are present: additional reporting positions and Savings interest are not
silently inferred. Its NIM-like result is after modeled Sky costs only, not the
Prime's full NIM. Existing reconciliation metadata is returned unchanged and is
explicitly deferred. Full reporting numerators use statement funding, not the
allocation subtotal. Mixed recognition in statements remains mixed recognition.

Formula mapping (A = matching average assets; F = average funding liabilities):
- yield = statement.interest / A; funding rate = statement.funding / F
- NIS = yield − funding rate; NIM = statement.net_interest / A
- NRM before incentives = (statement.net_revenue − direct expenses) / A
- NRM after incentives additionally deducts user incentives
- NM = (statement.net_revenue − all expense snapshot costs) / A

No aggregate percentage or YTD ratio is inferred from incomplete components.

## Validation

Unit/API tests cover dollar-weighted aggregation, deferred residuals, null versus
zero, duplicate/invalid capital inputs, expense leaf subtotals, contributor
compensation once, partial periods, revision validation, and request guards.
The retained live September reviewed statement was projected offline: its net
income equals the existing reporting-dashboard composition at full precision.
No production publication or settlement report was changed.

## Published average balances

A reporting-only worker now supplies complete daily EOD averages for a closed
month. `python -m settle.revenue.margin_balance_job --month 2026-09
--cache-dir /data/spark-margin-balances --publish` checkpoints each day and
reuses retained month-end stock observations. It does not run a settlement replay.

The asset denominator is the operational allocation book: configured investment
positions and idle balances, Savings idle buffers, PSM3 claims, additional WETH
and Steakhouse exposure and staked SKY. The existing reporting ownership adapter
removes designated Sky pool legs. Anchorage is added once using published daily
outstanding facility principal; escrow cash is not a proxy for loan principal.
Uninvested treasury tokens, MSC receivables and other accounting receivables are
outside allocated assets. Consequently these are management returns on allocated
assets, not returns on total balance-sheet assets or equity.

The funding-rate denominator uses actual Vat Art × rate and the six Savings
liabilities represented in `statement.funding`. Newer B/S-only Savings vaults are
not silently added to the denominator of expenses that omit their interest.
All numerator amounts, recognition policies, provisional expense assumptions,
settlement charges and reconciliation residuals remain unchanged.

The API populates its existing metrics only when all required daily components
are present. NIS subtracts the funding rate (funding expense / average funding)
from investment yield (investment income / average allocated assets). The other
ratios use average allocated assets. `balance_evidence` exposes component averages
and limitations. New selection tokens pin the balance publication; old selection
tokens continue to reproduce their original missing-denominator results.
