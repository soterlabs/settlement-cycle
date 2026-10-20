# Grove Steakhouse Prime: funding origin through August 2026

This extends the Base route audit in PR #215 to the Ethereum source and the
closing capital's origin. It uses reviewed normalized history through August
31, 2026, not newly fetched RPC data. Production CoF, settlements and API
revenue remain unchanged.

## Closing capital

| Origin | USD | Share of allocation capital |
|---|---:|---:|
| Confirmed original Sky borrowing | **193,695,912.39** | **95.5601%** |
| Identified gains reinvested into the vault | **1,485.61** | **0.0007%** |
| Existing source capital requiring further origin attribution | **8,997,920.75** | **4.4391%** |
| Total closing allocation capital | **202,695,318.75** | **100%** |

This answers funding origin, not which investments should bear outstanding
debt after repayments from other accounts. Those unrelated repayments do not
rewrite this allocation's origin. The allocation's own withdrawals release
weighted-average acquisition cost and its associated origin labels, consistent
with the existing local ledger. The identified gains are a minimum: unresolved
source cash may contain further gains.

## Direct borrowing evidence

The 45 distinct bridge claims total **221,694,379.874274**. Exactly
**212,696,459.128012** is linked to same-transaction borrowing. A draw is accepted
only when:

- The authenticated bridge claim has the expected amount and is the only
  positive destination.
- Per-ilk draws sum to the total minted amount, with one lending ilk.
- Net asset movements equal the draw, allowing only sub-cent numeric noise.
- There is no external-funding assumption, external lender or income component.

Existing cash in the same transaction is separately unresolved. A competing
destination prevents an exact attribution; mere coexistence with a draw does
not establish the source. All confirmed draws here are from ALLOCATOR-BLOOM-A.

Example:
[March 4 Ethereum draw and bridge](https://etherscan.io/tx/0x5c1628698549331e397fb1e7a771e5c240b58cb928ea2a2bb764545e68e6bde8)
creates 10m of debt and a 10m authenticated CCTP claim. The prior route audit
links the claim to Base cash and the 10m E23 deposit.

The March 27 10m bridge receipt funds two deposits (1m and 9m); claimed funding
is consumed once. Reusing a bridge claim beyond its source amount is rejected.

## Withdrawals and recycled gains

The local ledger has three withdrawals. Their released origin labels are
removed from the position. On April 10, one of those withdrawals returns
**1,005,620.125726** USDC:

- **1,004,134.513483584…** originally borrowed principal;
- **1,485.612242415…** gains.

That cash is combined with **3,994,379.874274** of newly borrowed/bridged cash
and redeposited as 5m. The principal keeps its source label; the gain remains
non-borrowed despite becoming part of a new vault contribution. The other
withdrawals are not automatically assigned to an arbitrary later deposit.

The exact transaction references and source components are retained in
`reconciliation/grove_steakhouse_funding_origins_2026_08.json`.

## Six remaining source transactions

All six amounts enter after the vault's final withdrawal, so they remain in
the August closing position. These are known immediate sources but unresolved
original borrowed-principal-versus-gain splits.

| Ethereum bridge date | Unresolved amount | Source evidence |
|---|---:|---|
| April 10 | 1,000,000.00 | Cash contains 402,742.96 explicitly classified income and 2,948,528.515755 withdrawn from E4, Grove x Steakhouse USDC on Ethereum. |
| May 11 | 1.00 | Apollo/Plume cash-settlement test return. The other 999,999 of this bridge is a direct draw. |
| May 17 | 2,000,000.00 | Cash contains a 24,987,498.885391 BUIDL redemption and 999,738.097233 from an AUSD conversion. |
| May 19 | 2,000,000.00 | Cash includes BUIDL/JTRSY redemption proceeds and earlier cash, followed by a partial ilk repayment. |
| June 13 | 997,919.746262 | Cash was funded by a Galaxy CLO boundary return and AUSD conversion, then partly used to repay the ilk. The bridge also contains a separately proven 2,002,080.253738 draw. |
| July 9 | 3,000,000.00 | After cash reached zero, a single Aave Horizon RLUSD disposal/conversion produced 4,999,128.526698 USDC; 3m was bridged. |

Bridge transactions, in table order:

- [April 10](https://etherscan.io/tx/0x470bf23cf0415a8be134dd5586e6807756f558a8468c1a57e25a9b8533375508)
- [May 11](https://etherscan.io/tx/0xb860d4e6f0bf0b4ddc074f29c67901ae8688fc2b35ab475617b6cd64bb64504b)
- [May 17](https://etherscan.io/tx/0x0e460b7d50a339e0d3be12d7435898c3315710c4cfd14b5bdb48ad99c2defb20)
- [May 19](https://etherscan.io/tx/0x53bb1be5d47512201ab92070a85ae911238daf6312a561e7eed35731bae216b0)
- [June 13](https://etherscan.io/tx/0xa7f029128c8493ba0f6691797fe222c39a3ee612ef10da15ee877e60424f004e)
- [July 9](https://etherscan.io/tx/0x4d3c436023bbf3be00947e1608785e12b2606364b9a5a6b361da55713c7fb099)

The retained cash windows begin at the most recent zero balance. Matching a
repayment amount to one earlier receipt does not prove that receipt alone
funded the repayment. No FIFO or newest-receipt-first assumption is introduced.
For the same reason, knowing a redemption's venue is not sufficient to call
all its proceeds borrowed principal.

Next investigate the July 9 Aave Horizon source first: it is the largest
remaining single-source path. Then resolve the BUIDL/JTRSY/AUSD cash windows.
No tracing beyond commingled FalconX deposit/return EOAs is required.

## Reproduction and validation

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_bridged_vault_funding.py \
  --history /tmp/pr215-grove-eoa/history.jsonl.gz --venue E23 --cash-venue E27 \
  --start 2026-01-01 --end 2026-08-31 \
  --output reconciliation/grove_steakhouse_funding_origins_2026_08.json
```

The artifact retains input/source hashes, source transactions, per-ilk draws,
cash windows, withdrawal cash origins and the component balance after each
allocation event. Their sum is checked against the independent local capital
ledger after every event.

`tests/fixtures/grove_steakhouse_funding_origins.jsonl.gz` is a compact subset
of already-normalized, already-adapted source evidence. The regression test
loads it directly, without applying adapters again or performing RPC queries.
It reproduces the closing split and all six unresolved source windows.
The compact fixture reproduces the complete full-history result, including
source windows. All 113 relevant tests pass, as does Ruff for the new files.
