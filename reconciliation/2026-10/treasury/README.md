# October 2026 consolidated Sky revenue and TMF

These are proposed MSC #14 amounts, not executed payments. The six prime
reports are unchanged. October consolidation reads their existing provenance
and `config/sky_total.yaml`: Reconciliation IV, Grove's Partner Rebate and
Osero's send-only refund enter exactly once through the reviewed mint/send
instructions. Step 1 is an allocation of the resulting net revenue, not an
additional expense deducted from the same revenue calculation.

Non-MSC was extracted for October only using the production HyperSync backend.
The existing method accrues stability fees and savings costs, and recognizes
other configured income/expense streams; it excludes allocator ilks from
non-MSC stability fees. No January–September report was regenerated.

TMF uses October's published 31-day SKY TWAP and October 31 aggregate backstop
capital from BA, retained in `ba-inputs.json`, plus archived Ethereum boundary
state and actual October SBE activity. It reads consolidated Sky net revenue,
rounded to whole USDS, without a separate SNR override. Proposed future engine
budgets and actual October SKY burn attribution are distinct.

## Reproduce

With the normal credentials loaded, from this branch:

```sh
PYTHONPATH=src python scripts/run_non_msc_2026.py --months 2026-10
PYTHONPATH=src python scripts/build_sky_total_2026.py --months 2026-10
PYTHONPATH=src python scripts/run_tmf_2026.py --months 2026-10
PYTHONPATH=src:scripts python scripts/retain_october_treasury.py
```

The consolidation step needs the six existing local October provenance files;
it does not rerun prime allocations. The retention script saves the three
outputs here and binds them to calculation sources, configurations, the
reconciliation ledger, prime preview and rendered summaries in `manifest.json`.

To reproduce the forum text offline from committed evidence:

```sh
PYTHONPATH=src:scripts python scripts/build_october_forum_draft.py
```

The builder rejects changed evidence, inconsistent prime instructions,
non-reconciling income/expense, or a TMF SNR that differs from consolidation.
Published-payment pins remain unset because these are draft instructions.
Independent daily asset controls remain deferred as previously authorized;
this does not waive the retained ALM transfer reviews.

## Results and warning review

| Component | USDS |
|---|---:|
| MSC net including adjustments | 17,607,810.00 |
| Non-MSC income | 17,179,738.09 |
| Non-MSC expense | 16,187,136.18 |
| Non-MSC net | 992,601.91 |
| Consolidated Sky net revenue | 18,600,411.91 |

The legacy-RWA warning is reviewed: [transaction 0x3aa1f62f398d4b3a6c396bc10c1ac5d8c270eb7b50e5d80e03b368779f1c610b](https://etherscan.io/tx/0x3aa1f62f398d4b3a6c396bc10c1ac5d8c270eb7b50e5d80e03b368779f1c610b)
calls `void()` (`0xac4c25b2`) on the configured RWA009-A / H.V.Bank jar
`0x6c6d4be2223b5d202263515351034861dd9afdb6`, at block 26284908.
Its successful receipt burns exactly 2,204,040.84 DAI and joins the same amount
to Vow through DaiJoin. This equals the non-MSC RWA jar income. RWA009-A
contributes no separate accrued stability-fee income in October, so this
receipt is not counted twice. Raw token-transfer and transaction-receipt
evidence is retained alongside the calculation. The generic tripwire remains
visible in the generated reports; it is not suppressed globally.

The other warning says there are no published mint/send pins yet. That is
expected for this draft: all six derived instructions match the reviewed prime
preview. TMF's SNR check passes, all BEAM bounds pass, and no executed November
parameter update is assumed. October activity contains 974 kicks.

## SKY rewards stream correction

The operator-supplied BA TMF Calculator changes stream sizing from the
September 2026 cycle: use actual cycle-month burn-window purchases allocated
to stakers, rather than dividing the future USDS budget by TWAP. The three-month
runway and 90-day duration remain unchanged. July and August keep TWAP sizing;
no previously published September artifacts are regenerated. Missing activity
fails instead of falling back to an estimate; an observed zero remains zero.

October's retained 42,736,162.038542855862345882 SKY purchases give
34,965,950.75880779116010117626 SKY for stakers, 104,897,852 SKY vest total,
and approximately 8,158,722 SKY per weekly distributor pull. SNR, prime
payments, USDS budgets, hop and burn attribution are unchanged. TWAP remains
in the model estimate and the USDS-farm APY valuation; the SKY-farm APY uses
the corrected rewards and actual month-end staked balance.

Recalculate without RPC/indexer calls:

```sh
PYTHONPATH=src python scripts/replay_october_tmf.py
PYTHONPATH=src:scripts python scripts/retain_october_treasury.py
```
