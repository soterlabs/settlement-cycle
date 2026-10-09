# Base Morpho: exact cash removes thousands of false funding receipts

The older Spark tracing snapshot already recognizes Base MetaMorpho performance
fees. Its simultaneous withdrawals, however, use the rounded one-share NAV
because `burned_shares == -net_share_change` fails whenever fee shares were also
minted. The correct relation is `burned_shares == fee_shares - net_share_change`.
The current normalizer's fix in `063186a` covers both MetaMorpho V1 and Vault V2;
the earlier input repair covered only the Ethereum V2 vaults.

This checkpoint applies the corresponding Base repair to a separate diagnostic
history. Canonical evidence covers **42,213** ALM withdrawal transactions;
**37,809** qualify for the pure-withdrawal-plus-fee branch. Their existing marks
understate outgoing cash by **3,216.620289341565 USDC** in aggregate. Against the
uncertainty-aware baseline, **15,979** unmatched receipts totaling
**3,159.908627566413 USDC** match these errors within one cent. These are gross
historical discrepancies, not monthly revenue or borrowing costs.

For example, [this withdrawal](https://basescan.org/tx/0x7166f2deac51f1eb5765e729ccc83b6b998afaac0f18c027b012127bda26708f)
actually returns **25,052,177.384853 USDC**. The old movement, excluding its
already recognized fee, is **25,052,153.849383759099**, creating a false
**23.535469240901** receipt. Actual fee shares are priced at the same execution
ratio as the withdrawal; they still create no borrowed principal.

The repair validates the V1 `AccrueInterest`/adjacent fee mint, actual Withdraw
events, owned-share net change and the old snapshot's fee/price relation.
Mixed deposits or other share transfers are left alone. If `old_debit` is the
old outgoing value and `cash` the observed proceeds:

```
new opening mark = old opening mark * cash / old_debit
new fee value    = cash * fee shares / burned shares
new share change = new fee value - cash
```

This preserves the redeemed share fraction while making the outgoing leg equal
the actual cash. Existing fee income is revalued by only **0.163383036578 USDC**
across the entire history; this is not new fee discovery or a revenue restatement.

An independent comparison of all **451,902** original/repaired batches confirms
that every debt field and every unrelated position is unchanged. The input
already includes the separately documented Ethereum V2 fee repair; both patch
provenances remain in the output header. The script refuses to overwrite its
source and replaces the output only after all expected transactions match.

Reproduce on the saved diagnostic inputs:

```sh
PYTHONPATH=src .venv/bin/python scripts/repair_spark_base_morpho_withdrawals.py \
  --history /tmp/pr215-spark-morpho-fees-history.jsonl.gz \
  --events tests/fixtures/spark_base_morpho_fee_withdrawals.json.gz \
  --output /tmp/pr215-spark-exact-withdrawals-history.jsonl.gz \
  --audit /tmp/pr215-spark-base-withdrawal-repair.json
```

The compact canonical fixture retains 179,429 vault logs from the relevant
withdrawal transactions, including fee accruals, share movements and ERC4626
events, through Base block **50,715,726**. It was extracted from the complete
709,106-log query for ALM share transfers, vault fee accruals and ALM ERC4626
entry/exit events. Whole old/repaired example batches are also retained.
The summary artifact is
`reconciliation/spark_base_morpho_withdrawal_repair_2026_08.json`.

Tests reproduce the real examples, preserve the share funding fraction, cover
fees larger than the withdrawal, reject inconsistent evidence and ensure a
failed completeness check leaves an existing verified output intact.

A separate full replay must measure the reconciliation effect. The amount
agreement above identifies false receipts; it does not certify allocation CoF.
Savings funding/refinancing and unassigned issuer cash remain unresolved.
Published settlements, API revenue and global borrowing costs are unchanged.
