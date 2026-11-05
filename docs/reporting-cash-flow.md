# Spark cash-flow reporting (2026)

Cash-flow publications use actual transaction dates, independently pinned historical balances, and a retained income/expense bridge. They never call the MSC worker or change settlement publications. The operational scope and known counterparties are in `src/settle/revenue/cash_flow/spark.json`; `layout.json` defines statement rows and hierarchy.

## API

| Route | Selection | Response |
|---|---|---|
| `GET /v3/reporting/cash-flow` | `prime=spark&year=2026`, optional completed-day `cutoff=2026-10-31` | Annual statement, monthly metrics, coverage, methodology, source manifest |
| Same route | `prime=spark&year=2026&revision=<selection>` | Immutable annual selection, including months that were absent |
| `GET /v3/reporting/cash-flow/events` | `prime=spark&month=2026-10&revision=<monthly revision>` | Paginated transaction/log evidence; optional `line`, `offset`, `limit` (1–1000) |
| `GET /v3/reporting/cash-flow/balances` | Same monthly identity | Independent opening/closing `balanceOf` witnesses and certified block pins |

The annual `selection` is distinct from the monthly `periods[].revision`. Save the annual selection for an export. Use monthly revisions for evidence. Invalid/cross-month identities fail closed. GETs read retained Postgres publications only; they never trigger RPC or indexer calls. ETags support conditional reads.

All values are decimal strings in USD at par for configured stablecoins. `available.value` is complete for its declared scope. `partial.known_value` is a known subtotal, not a certified total. `unavailable` is TBD; it is never zero. Future periods have no forecasts. Annual stock rows require the actual first/last elapsed boundary, rather than silently substituting another month.

## Sources and treatment

| Line/group | Method | Current limitation |
|---|---|---|
| Net income | Retained reporting net revenue less the approved provisional expense snapshot | Same cutoff required; wider operational-cluster income scope still incomplete |
| SPK / expenses booked | Same approved 2026 expense snapshot used by the dashboard | Provisional, not observed cash payments |
| Nonstable gains | Reverse retained income-statement unrealized gains | Wider treasury custody/disposal bridge incomplete |
| Nonstable transfers | Requires full-cluster historical cost basis | TBD, no fabricated zero |
| Other noncash | Known Savings interest accrual add-back | Other adjustments unresolved |
| Earned, not received | Retained published monthly MSC entitlement | Execution true-ups and wider income perimeter unresolved; missing publication is TBD |
| Sky receipts | Actual USDS mint to SubProxy matched to reviewed executed MSC amount/date; public settlement post splits demand vs allocation | Different amounts are unclassified, not assumed revenue |
| External receipts | Verified issuer reward payer; Anchorage cash after registered principal exclusions | Unidentified inflows stay unclassified |
| Expenses paid | Known payees, Savings paid interest, external payments from designated operating wallets | Generic operating-wallet classifications provisional |
| Allocation changes | Actual cash subscriptions/redemptions against reviewed venues | Gross redemption cash may contain yield; principal split and Sky-direct funding remain provisional |
| Investments | Actual registered investment cash legs | Approved immaterial Arkis follow-on retained separately in evidence |
| Net Sky borrowing | Actual AllocatorBuffer cash transfers | Direct Sky-funded seeds require further attribution |
| Savings deposits | Full Deposit/Withdraw/share-transfer histories; transferred shares carry weighted-average deposited principal | Unsupported/failed histories and queued Intents payouts remain incomplete; interest paid is operating |
| Sky contributions | Requires direct seed funding attribution | TBD |
| Buybacks | Stablecoin spent in a transaction receiving SPK into the owned cluster | Captures observable cash transactions within configured scope |
| Opening/closing cash | Independently pinned historical wallet/vault balances | Reward/principal subledger, transit and missing chains remain incomplete |

January MSC opening receivable: 7,071,339, supported by [MSC #4](https://forum.skyeco.com/t/msc-4-settlement-summary-november-and-december-2025-spark-grove/27617); actual cash arrived February 2, not January. July's Grove syrup acquisition retains both the cash borrowing and payment legs even though net cash is zero. No balancing plug is applied.

Authenticated bridge matching, issuer reward cash balances, nonstable historical cost basis, Savings Intents principal attribution and Sky-direct seeds are explicitly unresolved. Cash in transit is TBD until authenticated source/destination message matching is complete. Raw wallet cash remains a partial independent check. Unsupported XLayer/BSC/Gnosis sources are listed as missing, not inferred zero. `unclassified-net` and `gross-allocation-cash` are checks, not extra revenue.

## Publication / operation

```sh
python -m settle.revenue.cash_flow.job --from 2026-01-01 --to 2026-10-31 --dry-run --output-dir /tmp/cash-flow-preview
python -m settle.revenue.cash_flow.job --from 2026-01-01 --to 2026-10-31
```

Set existing provider and database environment variables securely; never put their values in config or command output. An optional `--env-file` is local only. `--evidence-dir` can replay captured public evidence; scope, interval, duplicate events and balance perimeter are checked before publication. The code version, configuration revision, input report/expense hashes, event digest and append-only revision are retained.

The dedicated Railway service runs daily at 22:15 UTC and refreshes the latest completed day plus the previous month-end. Older income corrections require an explicit historical replay; existing publications remain addressable. Successful source captures persist in a separate database cache, so a restarted process can reuse immutable Savings history. PostgreSQL advisory locking prevents competing publishers. Failures exit nonzero; missing sources remain visible in coverage. No credentials or provider URLs enter public evidence.
