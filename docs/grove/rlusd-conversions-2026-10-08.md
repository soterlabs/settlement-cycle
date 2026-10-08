# Grove Ripple conversions and returned test transfers

The [December 11, 2025 spell](https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20251211/GroveEthereum_20251211.sol)
configures `0xd178a90c41ff3dcffbfdef7de0baf76cbfe6a121` as the Ripple
RLUSD/USDC mint/burn wallet. These issuer-boundary cash receipts were missing
their outgoing RLUSD funding in the capital replay. The counterparty's internal
wallet activity is outside this scope.

Canonical ALM transfer logs identify 11 conversions from March through July
2026 and two returned $1,000 RLUSD test transfers. The conversions send
$323,285,911.10 RLUSD and receive $323,238,317.82 USDC, a $47,593.28 settlement
shortfall. The two additional $1,000 tests return in RLUSD, not USDC, and do
not create income. Each reviewed group finishes before the next begins.

These exact groups are encoded in `grove_rlusd_conversions.py`, with raw proof
in `tests/fixtures/grove_rlusd_conversion_events.json`. There is no runtime
amount/date matcher. Payment and return amounts, asset contracts, addresses,
blocks and log order are checked by regression tests. As with the other issuer
links, the configured corridor and single outstanding group support the mapping;
there is no common on-chain request identifier from the off-chain issuer.

Several conversions settle through multiple USDC payments. The July 20 $65m
request settles on July 21 in five payments, including two separate $20m
transactions in the same block. Replay ordering uses block/log identity rather
than requiring distinct timestamps. The July 21 $20,285,911.10 conversion also
returns in multiple payments. Neither July conversion has a cash shortfall.

A pending claim receives only the original outgoing funding basis. Partial
payments release principal at par and leave the unpaid claim outstanding. Only
the final observed payout closes the claim and realizes any demonstrated
shortfall under the existing ledger rules. If the history ends before that
payout, the remaining principal stays pending; no future cash is fetched or used
to write off a fee early. Known earned cash never becomes borrowed principal.

## August replay result

| Metric | Before | After |
|---|---:|---:|
| Unmatched receipts | 217 | 186 |
| Unmatched outflows | 438 | 417 |
| STAC E7 modeled borrowing costs | $237,786.98 | $273,724.44 |
| BUIDL E10 signed modeled cost after known SDE deductions | −$444,397.86 | −$236,852.13 |
| Apollo E22 modeled borrowing costs | $56,373.76 | $64,883.40 |
| Fully eligible allocation borrowing costs | $1,233.51 | $1,233.51 |

The fixes restore funding to later cash, repayments and allocations. The
additional modeled amounts are not newly validated charges. BUIDL's negative
signed diagnostic is still incomplete funding against full known SDE dollar
deductions, not a payable credit. Full-history outer bounds remain broad and
unchanged because other routes remain unresolved.

Per-ilk reconciliation is still incomplete: BLOOM-A's eligible subtotal is $0
against $3,448,941.99 excluding MSC; GROVE-A's is $1,233.51 against $11,784.74.
No residual is assigned to force a match. Settlement reports and API data remain
unchanged. Spark has not been replayed in this step.

Evidence and input/code hashes:
[grove_rlusd_conversions_2026_08.json](../../reconciliation/grove_rlusd_conversions_2026_08.json).
Tests cover every canonical conversion, fees, same-token refunds, mixed earned/
borrowed funding, overnight settlement, same-block payouts, partial pinned
histories, missing source/payout events, changed amounts and idempotence.
