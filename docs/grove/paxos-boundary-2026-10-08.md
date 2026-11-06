# Paxos bridge entrypoint accounts for the remaining large cash outflows

Five ALM USDC payments on July 21/29 total **$15,000,100**: $100, $1,000,
$2,499,000, $5,000,000 and $7,500,000. All go to
`0x8C0A9E5939B97979f85d9aDA3d983C6E713Cc2dB`, explicitly named
`PAXOS_USDC_DEPOSIT_WALLET` by the
[July 16 Grove spell](https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20260716/GroveEthereum_20260716.sol).
Each payment is funded by an equal BLOOM-A draw in its transaction. The full
Ethereum ALM USDC transfer inventory contains no return from this wallet through
August 31.

Per the operator's EOA policy, allocation begins when cash reaches this deposit
wallet. The capital tracer now recognizes this financing boundary, including
future same-wallet returns. It does not trace commingled wallet interiors or
assume that a particular Robinhood position received the money. Different return
addresses require evidence before being linked. No USDG yield is inferred.

`E_PAXOS_BRIDGE` is a tracing-only allocation, separate from the inactive E39
revenue stub. Its net PnL and APYs remain unavailable. The actual boundary
transfers are retained and checked in
`tests/fixtures/grove_paxos_boundary_events.json`; historical constants apply the
same logic to saved histories. Returns release principal up to outstanding
payments and classify only the excess as gain, following the existing EOA rule.

August replay removes five outflow gaps (**244 → 239**), including the three
largest remaining ones. There are still **22** receipt gaps. Modeled Paxos cost
is **$46,457.60**, but this is **not eligible/fully verified**: subsequent
repayments funded by unresolved cash elsewhere propagate origin uncertainty.
Eligible Grove costs remain **$11,784.59**, all from GROVE-A; BLOOM-A is unresolved.

Evidence: `reconciliation/grove_paxos_boundary_2026_08.json`. Published reports,
API revenue, debt and global financing charges are unchanged.
