# Grove E22 historical RPC / NAV comparison

Read-only checks on 2026-09-30. No RPC configuration was changed and no revenue
job or API publication was started.

Plume's [official mainnet endpoint](https://www.plume.org/blog/genesis),
`https://rpc.plume.org`, responds with chain ID `0x18232` (98866). It supports
historical `eth_call balanceOf` for Grove's configured Plume ALM holder and
ACRDX (`0x9477724bb54ad5417de8baff29e59df3fb4da74f`) at:

| Boundary | Plume block | Ethereum NAV block | ACRDX shares |
|---|---:|---:|---:|
| September opening | 90,704,090 | 25,878,704 | 20,201,743.292497372656956881 |
| September 25 closing | 95,548,352 | 26,057,904 | 20,201,743.292497372656956881 |
| September 29 closing | 96,358,377 | 26,086,569 | 20,201,743.292497372656956881 |

The configured dRPC endpoint returned HTTP 400 / JSON-RPC -32601 (`eth_call`
unavailable) at the opening and September 29 closing blocks. Its credentialed
URL is intentionally omitted. The successful public endpoint establishes an
alternative for these historical reads, not a guarantee of production capacity.

NAVs were read independently from Ethereum at the corresponding pinned blocks:
Chronicle router `0x87603527aebbbdf46d73e524830be81f93778ffa` (`read`, 18 decimals)
and the configured fallback vault
`0x74a739ea1dc67c5a0179ebad665d1d3c4b80b712`
(`convertToAssets(10**18)`, USDC 6 decimals).

| Boundary | Chronicle primary | ERC-4626 fallback |
|---|---:|---:|
| September opening | 1.02485174998024 | 1.024851 |
| September 25 closing | 1.02181216620581 | 1.021812 |
| September 29 closing | 1.02047075606524 | 1.020470 |

Using the fallback at both endpoints changes the zero-net-flow revenue estimate
by **+$11.79 through September 25**, or **−$0.12 through September 29**. These
small differences reflect the vault's six-decimal price precision at these pins.
This is not evidence that every intra-period vault price equals Chronicle.

The published September 25 E22 result already matches the primary readings:
opening value $20,703,791.96596750779042466108, closing value
$20,642,387.07484043269094316981, net flows zero, revenue
−$61,404.89112707509948149127. Revision:
`6856244174667525ae48901c5516b9fc2d61f0da0ac80032989fa23d847e4d11`.
The failed retry therefore does not establish that the existing September 25
API result used a fallback or was incorrect.

There are two distinct failures to distinguish: a NAV fallback can replace a
failed price read, but cannot supply a missing Plume token balance. The separate
`const_one` ($1) fallback is for pre-vault history and is not justified by this
September comparison.
