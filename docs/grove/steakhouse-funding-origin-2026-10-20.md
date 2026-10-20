# Grove Steakhouse Prime: funding origin through August 2026

This extends the Base route audit in PR #215 to the Ethereum source and the
closing capital's origin. It uses reviewed normalized history through August
31, 2026, not newly fetched RPC data. Production CoF, settlements and API
revenue remain unchanged.

## Closing capital

| Origin | USD | Share of allocation capital |
|---|---:|---:|
| Traced original Sky borrowing | **202,316,398.04** | **99.8131%** |
| Identified gains reinvested into the vault | **201,059.46** | **0.0992%** |
| Existing source capital requiring further origin attribution | **177,861.25** | **0.0877%** |
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

Existing cash in the same transaction requires separate upstream attribution. A competing
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
original borrowed-principal-versus-gain splits. The amounts below are residuals
after scoped borrowing and identifiable-gain attribution.

| Ethereum bridge date | Unresolved amount | Source evidence |
|---|---:|---|
| April 10 | 59,271.894810 | Cash contains 402,742.96 explicitly classified income and 2,948,528.515755 withdrawn from E4, Grove x Steakhouse USDC on Ethereum. |
| May 11 | 1.00 | Apollo/Plume cash-settlement test return. The other 999,999 of this bridge is a direct draw. |
| May 17 | 1,376.307847 | Cash contains a 24,987,498.885391 BUIDL redemption and 999,738.097233 from an AUSD conversion. |
| May 19 | 67,667.633147 | Cash includes BUIDL/JTRSY redemption proceeds and earlier cash, followed by a partial ilk repayment. |
| June 13 | 3.799349 | Cash was funded by a Galaxy CLO boundary return and AUSD conversion, then partly used to repay the ilk. The bridge also contains a separately proven 2,002,080.253738 draw. |
| July 9 | 49,540.615296 | The 3m Horizon reallocation now has 2,914,315.648029 traced borrowed origin and 36,143.736675 identified gains. The remaining source uncertainty is retained. |

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

## Reusable upstream reallocation tracking

The diagnostic supports explicit `--reallocation-venues E1`. This is executable
source tracking, not a manually inserted number. It computes the source venue's
acquisition-cost ledger, then accepts a link only when:

- Cash starts empty and receives exactly one identifiable allocation disposal.
- The bridge spends that isolated receipt, with no intervening cash movement
  or other source/destination that would make the attribution ambiguous.
- The source disposal has an isolated cost release in its allocation ledger.

For a partial cash spend, the code carries the receipt's proportional
acquisition-cost/net-gain split, consistent with weighted-average accounting.
This is an explicit accounting convention, not proof that particular token
units represent principal or yield. The code preserves the original-principal
uncertainty; source acquisition cost alone is not proof of Sky borrowing.

For the [July 9 Horizon disposal](https://etherscan.io/tx/0x83a020f139c7cdafebb5f9746b7e9cf71874625dcf1747e3f9084638322e74ea):

| Component | Full cash receipt | 3m bridged portion |
|---|---:|---:|
| Acquisition cost recovered | 4,938,899.465006 | 2,963,856.263325 |
| Net gain after conversion | 60,229.061692 | 36,143.736675 |
| Cash | 4,999,128.526698 | 3,000,000.000000 |

The source aToken value removed was 5m, but actual USDC proceeds were lower.
Using token mark instead of actual cash would overstate the realized gain.
The source ledger excludes 1,800,219.702380 of classified Horizon reward receipts
through July 9 from acquisition cost.

## Horizon acquisition funding: scoped RLUSD origin ledger

The April 22–25 Horizon deposits (50m, 22.5m, 33m and 9.243m) came through
idle RLUSD after withdrawals from Grove's Aave Ethereum RLUSD position.
The opening Horizon position also includes an Ethereum-Aave-to-Horizon
transfer. Treating those inflows as unrelated capital would lose their origins.

`audit_scoped_borrowing_origins.py` therefore tracks just E1 (Horizon),
E3 (Aave Ethereum RLUSD), E13 (idle RLUSD) and their configured custody:

- Internal transfers carry the weighted-average borrowed-origin fraction.
- Explicit rewards and passive growth never add borrowed basis.
- For fresh contributions, competing destinations and unexplained execution
  shortfalls consume draws first. Only the remaining draw is attributed.
- Known swap income is removed from the capital-flow arithmetic; it neither
  inflates borrowed principal nor prevents attribution of the genuine draw.
- Repayments outside this group do not rewrite its original funding labels.
- Pre-2026 movements establish opening sources only; no 2025 settlement is
  recalculated.

This is a conservative attribution under the stated weighted-average transfer
policy, conditional on normalized inputs. It is not proof of unique physical
token provenance through fungible pools. Under it, the July 9 3m transfer splits:

| Origin | USD |
|---|---:|
| Traced original borrowing | **2,914,315.648029** |
| Net gains | **36,143.736675** |
| Unresolved original principal | **49,540.615296** |

The artifact contains every scoped transaction's draw contribution, withdrawn
borrowed basis and resulting account balances. The largest unresolved additions
upstream include the December 16, 2025 STAC-related mixed transaction and
January 24, 2026's combined Horizon contribution/JTRSY delivery. The previously
deferred May 7 RLUSD receipt remains unclassified; no new business judgment is
silently introduced to close the gap.

## Mixed BUIDL/JTRSY/AUSD cash attribution

A separate scoped origin ledger covers E4, E9, E10, E14, E15, E21 and E36:
Ethereum Steakhouse USDC, JTRSY, BUIDL, AUSD/conversion custody, Ethereum
USDC cash, Galaxy CLO and FalconX. Galaxy and FalconX stop at their configured
EOA allocation entrypoints; their commingled wallets are not traced internally.
The Horizon/RLUSD trace remains separate.

This adds **5,706,170.00** of traced original borrowing and **163,430.12** of
identified gains relative to the prior 97% result. These percentages describe
E23 closing capital, not Grove's total debt.

Cash windows start at zero and must be continuous through receipts, repayments
and bridge spends. Each withdrawal removes the weighted-average mix. A repayment
matching one receipt's amount does not select that receipt: this matters for
May 19 and June 13. Explicit income and isolated disposal proceeds above released
acquisition cost identify gains; internal custody deliveries do not. Unknown
receipts remain unresolved. Borrowed basis and identified gains cannot overlap
or exceed the cash spent.

This is an accounting convention supported by normalized transaction evidence,
not unique provenance of fungible token units. Identified gains remain a minimum
where source acquisition costs themselves contain reinvested gains. The artifact
retains the full cash windows and scoped ledger.

Next investigate April 10's E4 source ($59,272), May 19's BUIDL/JTRSY source
($67,668), and July 9's Horizon source ($49,541). These dominate the remaining
$177,861; May 11's $1 test remains explicitly unresolved.

## Reproduction and validation

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_bridged_vault_funding.py \
  --history /tmp/pr215-grove-eoa/history.jsonl.gz --venue E23 --cash-venue E27 \
  --reallocation-venues E1 \
  --origin-group E1 E3 E13 \
  --cash-origin-group E4 E9 E10 E14 E15 E21 E36 \
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
The fixture includes the RLUSD and mixed-cash groups (1,453 normalized batches). It reproduces
the complete full-history result, including source windows and the upstream
reallocation and scoped origin ledger. All 127 relevant tests pass, as does
Ruff for the changed files.
