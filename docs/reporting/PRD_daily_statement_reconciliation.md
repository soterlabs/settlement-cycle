# PRD: daily statement reconciliation

Status: proposed for later implementation. Start with Spark, then extend to other Primes. Detect accounting gaps during the month using retained reporting/settlement API data. Fetch targeted chain evidence only when needed; do not replay full history for each check.

| Check | Daily comparison | Detects |
|---|---|---|
| Debt | Opening debt + draws − repayments + settlement debt + rate accrual = closing `art × rate` | Missing debt movements; normalized/real debt confusion |
| SDE | Gross debt − historical SDE legs = reported net debt; identical legs excluded from assets | Double deduction; wrong designation dates; wrong LP leg |
| MSC gross balances | Opening receivable/payable + earned-month obligations/corrections − executed legs = closing balances, separately | Netting; duplicate settlement; wrong cut-off |
| Savings | Gross vault claims − controlled-holder claims = external deposits; reconcile all configured chains | Missing vaults; internal claims counted as external liabilities |
| Positions | Closing value + distributions − opening value − net principal inflows = return, with FX/valuation separately identified | Duplicate income; deposits treated as revenue; missing withdrawals |
| Issuer rewards | Opening claim + accrual − matched receipts = closing claim/prepaid, per issuer | Missing receipts; duplicated rewards; unsupported accrual windows |
| Cash | Opening cash + external inflows − external outflows + valuation = closing cash; internal transfers eliminate | Stale idle balances; missing bridge legs; double-counted custody |
| Equity | Opening equity + income + contributions + token awards − buybacks − dividends = closing equity; compare independently with assets − liabilities | Missing expenses/equity movements; artificial balancing plugs |

## Operating rules

- Use common UTC cut-offs, explicit chain pins, publication revisions, accounting periods and historical ownership policy. Mixed or stale sources produce **incomplete**, never a passing zero.
- Give each obligation, transfer and income component a stable identity. Report unmapped flows and reused identities separately from arithmetic residuals.
- Check absolute and relative residuals; propose thresholds from observed rounding and timing differences before enabling alerts. Never hide small holdings from totals.
- Retain expected, actual, residual, source references and the smallest actionable exception set. Separate known timing differences from unexplained gaps; track owner and resolution.
- Opening equity and off-chain contract balances need approved evidence. A chain transfer proves payment, not an investment's legal terms or fee agreement.
- Keep monitoring read-only. Corrections require review; checks must not rewrite settled months automatically.

## Acceptance

Demonstrate failures for a duplicated issuer receipt, stale ALM balance, omitted chain/vault, wrong SDE date, repeated MSC execution and double-counted internal transfer. A corrected case must pass on the same retained inputs. Missing coverage must remain visibly incomplete. Expose a compact daily result endpoint if existing APIs cannot supply it.
