# Settlement Reconciliation — MSC #5–#13 (January–September 2026)

**Working draft — calculations pending the Spark/Obex async extension.**

This post will reconcile newly identified January–September settlement
corrections against the reports used at
[`536f149`](https://github.com/soterlabs/settlement-cycle/commit/536f1492089ed82fbf6eaabb956f7a194b887d47).
It follows the [January–August reconciliation](https://forum.skyeco.com/t/settlement-reconciliation-msc-5-12-january-august-2026/28273).
Previously credited items remain in that baseline and will not be paid again.

The completed draft will include monthly amounts and settlement effects for:

- Spark: native-USDS bridge CoF, September RLUSD partner receipts, the missing
  RLUSD investments, and any historical async-vault corrections from #263.
- Grove: async redemption and issued-deposit CoF, missing Base and Diamond LP
  income, and the net small-BUIDL capital/fee correction.
- Osero: the executed July–September duplicate shortfall deductions.
- Obex: any measured historical async-vault correction from #263.

Each line will distinguish additional prime revenue, a Sky-share/debt adjustment
and a send-only refund. The final settlement effects will show both Sky's net
position and the impacted prime's mint/transfer adjustments using the existing
MSC mechanism and rounding convention.

October receipts, including partner rebates, belong to the ordinary October
MSC post. Existing historical credits already configured in October will be
identified and included once. No final reconciliation instruction is proposed
by this unfinished draft.
