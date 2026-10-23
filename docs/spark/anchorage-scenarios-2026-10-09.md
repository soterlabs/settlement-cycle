# Anchorage: isolate the unconfirmed principal/interest splits

The two unresolved atomic payments are documented in `QUESTIONS.md S33`:

| Receipt | Scenario A principal / interest | Scenario B principal / interest |
|---|---:|---:|
| July 16: 10,036,438 USDC | 10,000,000 / 36,438 | 10,000,000 / 36,438 |
| August 17: 51,267,944 USDC | 50,000,000 / 1,267,944 | 51,000,000 / 267,944 |

Scenario A's August interest resembles the separately observed monthly sweeps;
that supports it as a working hypothesis, not proof. The atomic transfers do
not identify which part is principal. A third scenario treats the entire
receipts as principal, matching the published conservative classification.
None of these splits is automatically activated in extraction or replay.

`apply_anchorage_scenario()` is an explicit diagnostic option. It releases the
assumed principal from the cash-funded facility, labels only the residual as
income, adjusts subsequent facility opening marks, and propagates the
assumption to affected funding. It leaves the facility's unsupported status
and all Sky debt events unchanged. It requires both exact observed receipts,
sufficient previously deposited cash, and an unrepaired input.

After the independently identified July 21 duplicate-disbursement refund:

| Scenario | Closing facility cash principal | August average cash principal | All-Sky-funded cost redistribution sensitivity |
|---|---:|---:|---:|
| A: 10m / 50m principal | 210,133,608.694857 | 235,940,060.307760 | 106,227.65 |
| B: 10m / 51m principal | 209,133,608.694857 | 235,456,189.340018 | 107,731.22 |
| Entire receipts principal | 208,829,226.694857 | 235,290,101.017438 | 108,247.23 |

These are **cash principal**, not certified Sky-funded basis. The last column
weights the principal released versus leaving these two returns unmatched by
August's unchanged daily borrowing rates. It assumes all that principal was
Sky-funded and is a facility cost attribution sensitivity, **not savings in
Spark's aggregate Sky expense**. The actual borrowing-cost redistribution
requires a complete funding replay, because returned cash can be reinvested,
held idle, or used to repay a lender.

The A/B split changes this sensitivity by only **1,503.57 USD**. Connecting the
large returned principal to its original funding matters much more than the
remaining split ambiguity. Scenario A remains provisional until the loan
statement or counterparty confirms the split.

Reproduce the measured cash ledger with `scripts/audit_spark_anchorage_scenarios.py`;
`reconciliation/spark_anchorage_scenarios_2026_08.json` records daily balances and
input hashes. Receipt rows and original normalized batches are preserved in
`tests/fixtures/spark_anchorage_ambiguous_returns.json`. Tests check conservation
of cash and Sky principal, subsequent marks, uncertain status, repeat-application
rejection, and rejection of changed or unfunded receipts.
