# Spark August idle-USDC yield exposure

S45 (Arbitrum), S53 (Unichain) and S55 (Avalanche) held USDC during August even
though month-end balances were near zero. The published denominator therefore
understated exposure. S45's annualized net yield was undefined; S53/S55 had no
yield denominator.

Use the arithmetic average of 31 daily closing USDC balances at par. This is
the full position value, including Savings-funded balances, not Sky principal
alone. Non-USDS idle stablecoins receive no idle-USDS deduction. Gross revenue
is zero. Preserve existing conditional borrowing costs and their uncertainty;
this correction does not certify their funding split or change settlement.

| Venue | Average cash USD | Conditional borrowing cost USD | Gross APY | Conditional net APY |
|---|---:|---:|---:|---:|
| S45 | 2,163.569508 | 5.701125 | 0% | -3.058899% |
| S53 | 578.386186 | 1.241371 | 0% | -2.498042% |
| S55 | 1,198.301396 | 0.072971 | 0% | -0.071676% |

Net here means after Sky borrowing costs; it excludes Savings VSR costs.
Canonical borrowing costs/net yields remain unavailable pending funding validation.

## Evidence and reproduction

`reconciliation/spark_idle_usdc_august_exposure.json` pins the 93 daily balances,
account addresses and hashes of the saved normalized history and selected-account
snapshot. Independently stream movements in replay order, carry balances forward,
and compare `value_before + change` against both the saved observed asset value
and the pinned daily cash evidence. No provider reads or principal-origin replay
are needed. The check also requires zero external-income movements.

```sh
PYTHONPATH=src python scripts/check_spark_idle_usdc_exposure.py \
  --history /tmp/spark-adapted-origin-history.jsonl.gz \
  --snapshot /tmp/spark-mixed-funding-corrected-2026-08.replay.json.gz
```

`repair_spark_idle_usdc_august.py --base <complete-live-document> --output <reviewed.json>`
prepares a revision; `--publish` explicitly publishes it. The publisher locks
monthly publication, rebuilds from the stored baseline, rejects stale revisions,
and makes retries idempotent. Only these three allocation rows and repair
provenance change. Published revenue, costs, all other rows (including S66),
global financing metadata and historical settlements remain unchanged.

## Remaining work

The complete-account August funding check must separate the $7.61M Savings gap
into financing, other custody and actual unrepresented principal. The $231k
financing cost must remain separate from already identified MSC and Vat accrual.
An arithmetic bridge alone does not certify allocation attribution.

September capture is incomplete: the last saved log reached Base transaction
237,600/314,479 on November 2. Its process was no longer running on November 3.
August funding evidence must not be transplanted into September. Resume capture
with persisted provider inputs and September settlement block pins before
publishing September conditional or canonical mixed-funding calculations.
