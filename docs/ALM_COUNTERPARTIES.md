# ALM-Proxy historical counterparties

Every address that has ever transferred tokens to or from a Sky Prime Agent ALM Proxy, grouped by prime + chain.

- **Source:** Dune query [7357558](https://dune.com/queries/7357558)
- **Snapshot date:** 2026-04-22
- **Scan range:** 2024-08-01 → present
- **Chains covered:** ethereum, base, arbitrum, optimism, unichain, avalanche_c, plume, monad
- **Spam filter:** excluded rows with `inbound + outbound < $1,000` (drops ~70 rows of airdrop/phishing noise)
- **Rows retained:** 132

## ALM addresses in scope

| Prime | Chain | ALM |
|---|---|---|
| OBEX | ethereum | `0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2` |
| Grove | ethereum | `0x491edfb0b8b608044e227225c715981a30f3a44e` |
| Grove | base | `0x9b746dbc5269e1df6e4193bcb441c0fbbf1cecee` |
| Grove | avalanche_c | `0x7107dd8f56642327945294a18a4280c78e153644` |
| Grove | plume | `0x1db91ad50446a671e2231f77e00948e68876f812` |
| Grove | monad | `0x94b398acb2fce988871218221ea6a4a2b26cccbc` |
| Spark | ethereum | `0x1601843c5e9bc251a3272907010afa41fa18347e` |
| Spark | base | `0x2917956eff0b5eaf030abdb4ef4296df775009ca` |
| Spark | arbitrum | `0x92afd6f2385a90e44da3a8b60fe36f6cbe1d8709` |
| Spark | unichain | `0x345e368fccd62266b3f5f37c9a131fd1c39f5869` |
| Spark | optimism | `0x876664f0c9ff24d1aa355ce9f1680ae1a5bf36fb` |
| Spark | avalanche_c | `0xece6b0e8a54c2f44e066fbb9234e7157b15b7fec` |

Plume ALM returned no rows (cross-chain indexing gap; see [grove/QUESTIONS.md](grove/QUESTIONS.md) §Q4). Keel, Prysm, Skybase not yet in scope.

## Shared Sky infrastructure counterparties

These three addresses appear across **every** Ethereum ALM (OBEX + Grove + Spark). They are the Sky-native routing contracts, not venues:

| Address | Role | Evidence |
|---|---|---|
| `0x37305b1cd40574e4c5ce33f8e8306be057fd7341` | LitePSM-USDC (USDC ↔ DAI atomic swap) | inbound USDC + equal outbound across all three primes |
| `0xf6e72db5454dd049d0788e411b06cfaf16853042` | DaiUsds converter (DAI ↔ USDS) | inbound DAI + equal outbound USDS |
| `0x3225737a9bbb6473cb4a45b7244aca2befdb276a` | AllocatorVault USDS dispenser | outbound-only, matches `frob` mint volumes |

Cross-prime overlap confirms these are not venue-specific.

## Labelled counterparties

Known labels derived from stars-api allocation addresses, ALM address table, and on-chain context:

| Address | Label |
|---|---|
| `0x0000000000000000000000000000000000000000` | ERC-20 mint/burn (issuance, redemption) |
| `0x37305b1cd40574e4c5ce33f8e8306be057fd7341` | Sky LitePSM-USDC |
| `0xf6e72db5454dd049d0788e411b06cfaf16853042` | Sky DaiUsds converter |
| `0x3225737a9bbb6473cb4a45b7244aca2befdb276a` | Sky AllocatorVault USDS dispenser |
| `0x629ad4d779f46b8a1491d3f76f7e97cb04d8b1cd` | Grove AllocatorBuffer (from [grove/README.md](grove/README.md)) |
| `0x51e9681d7a05abfd33efafd43e5dd3afc0093f1d` | OBEX AllocatorBuffer (symmetric to Grove) |
| `0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b` | Maple syrupUSDC vault (OBEX venue) |
| `0x1601843c5e9bc251a3272907010afa41fa18347e` | Spark Ethereum ALM (appears as counterparty on Spark Base = cross-chain hub) |
| `0x49506c3aa028693458d6ee816b2ec28522946872` | Anchorage custodial (Spark $150M off-chain BTC position) |
| `0xfc0539d019482d311c161ae3b756cdccdec45e87` | PayPal-related PYUSD feeder (not a direct EOA transfer; most PYUSD arrives via mint) |
| `0x0000000005f458fd6ba9eeb5f365d83b7da913dd` | Janus Anemoy JAAA/JTRSY issuer |
| `0x000000000004444c5dc75cb358380d2e3de08a90` | Uniswap V4 PoolManager (likely) |

---

## OBEX — Ethereum (`0xb6dD7ae2…f715A2`)

| Counterparty | Label | In (USD) | Out (USD) | Tokens |
|---|---|---:|---:|---|
| `0x37305b1cd40574e4c5ce33f8e8306be057fd7341` | LitePSM-USDC | 600,148,672 | 0 | USDC |
| `0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b` | Maple syrupUSDC vault | 0 | 600,148,672 | USDC |
| `0x0000…0000` | mint (DAI↔USDS, syrupUSDC shares) | 600,146,427 | 0 | DAI, syrupUSDC |
| `0xf6e72db5454dd049d0788e411b06cfaf16853042` | DaiUsds converter | 0 | 600,146,427 | DAI |
| `0x3225737a9bbb6473cb4a45b7244aca2befdb276a` | AllocatorVault dispenser | 0 | 600,124,560 | USDS |
| `0x51e9681d7a05abfd33efafd43e5dd3afc0093f1d` | OBEX AllocatorBuffer | 600,124,560 | 0 | USDS |

6 counterparties. Closed-loop: single venue (Maple syrupUSDC), all flows tied to the same $600M peak debt.

---

## Grove — Ethereum (`0x491edfb0…f3a44e`)

| Counterparty | Label | In (USD) | Out (USD) | Tokens |
|---|---|---:|---:|---|
| `0x0000…0000` | mint/burn | 4,957,719,777 | 0 | 15 tokens (AUSD, BUIDL-I, JAAA, JTRSY, STAC, USDC, USDS, aEth*, grove-bbq*, AUSDUSDC, …) |
| `0x37305b1cd40574e4c5ce33f8e8306be057fd7341` | LitePSM-USDC | 3,671,516,011 | 1,267,274,732 | USDC |
| `0xf6e72db5454dd049d0788e411b06cfaf16853042` | DaiUsds converter | 1,267,206,222 | 3,671,462,241 | DAI |
| `0x629ad4d779f46b8a1491d3f76f7e97cb04d8b1cd` | Grove AllocatorBuffer | 3,671,223,636 | 1,267,249,267 | USDS |
| `0x3225737a9bbb6473cb4a45b7244aca2befdb276a` | AllocatorVault dispenser | 0 | 4,938,429,858 | DAI, USDS |
| `0x0665fde254598e307b63f3aae3ccd881a62d4be3` | JTRSY/USDC redemption endpoint | 0 | 940,477,241 | JTRSY, USDC |
| `0xd001ae433f254283fece51d4acce8c53263aa186` | RLUSD/USDC routing | 439,252,875 | 439,486,117 | RLUSD, USDC |
| `0x0000000005f458fd6ba9eeb5f365d83b7da913dd` | Janus Anemoy issuer (JAAA/JTRSY) | 50,141,352 | 750,026,176 | JAAA, JTRSY, USDC |
| `0xd1917664be3fdaea377f6e8d5bf043ab5c3b1312` | **unresolved — also Spark ETH counterparty** | 0 | 800,087,454 | USDC |
| `0xfa82580c16a31d0c1bc632a36f82e83efef3eec0` | RLUSD venue | 288,429,693 | 284,605,872 | RLUSD |
| `0x43d51be0b6de2199a2396ba604114d24383f91e9` | JAAA/JTRSY off-ramp | 0 | 540,073,075 | JAAA, JTRSY, USDC |
| `0xcfc0f98f30742b6d880f90155d4ebb885e55ab33` | **unresolved — also Spark ETH counterparty** | 434,821,198 | 0 | USDC |
| `0x040170aa9aaa916c2e8135777a31f17c440ba52a` | JAAA venue | 326,823,436 | 0 | JAAA, USDC |
| `0x92e75576f81838df5d019940e740117f57924e9b` | USDC inbound source | 300,146,038 | 0 | USDC |
| `0xd178a90c41ff3dcffbfdef7de0baf76cbfe6a121` | RLUSD/USDC routing | 144,968,383 | 144,991,918 | RLUSD, USDC |
| `0xe3190143eb552456f88464662f0c0c4ac67a77eb` | RLUSD routing | 10,001,579 | 144,098,521 | RLUSD |
| `0x51e4c4a356784d0b3b698bfb277c626b2b9fe178` | USDC outbound | 0 | 100,023,421 | USDC |
| `0xfd78ee919681417d192449715b2594ab58f5d002` | USDC outbound (bridge candidate — also appears on Grove Base) | 0 | 87,696,433 | USDC |
| `0xbafead7c60ea473758ed6c6021505e8bbd7e8e5d` | AUSD/USDC routing | 36,121,785 | 48,012,226 | AUSD, USDC |
| `0x748b66a6b3666311f370218bc2819c0bee13677e` | USDC routing | 24,552,202 | 29,002,111 | USDC |
| `0xb52845f26bb7a4bf0638ab778e220b56565066d2` | USDC inbound | 50,024,095 | 0 | USDC |
| `0xd94f9ef3395bbe41c1f05ced3c9a7dc520d08036` | USDC outbound | 0 | 50,013,392 | USDC |
| `0x2e3a11807b94e689387f60cd4bf52a56857f2edc` | USDC outbound | 0 | 49,912,537 | USDC |
| `0xbeeff08df54897e7544ab01d0e86f013da354111` | USDC routing | 20,041,280 | 20,013,440 | USDC |
| `0xbeef2b5fd3d94469b7782aebe6364e6e6fb1b709` | USDC routing | 16,017,795 | 16,000,013 | USDC |
| `0x68215b6533c47ff9f7125ac95adf00fe4a62f79e` | USDC routing | 11,625,369 | 11,505,474 | USDC |
| `0xe79c1c7e24755574438a26d5e062ad2626c04662` | AUSD/USDC routing | 4,247,763 | 12,503,288 | AUSD, USDC |
| `0xac3d86f9840a8be07de5f67d6427983b7009df1b` | USDC inbound | 1,378,562 | 0 | USDC |
| `0x1157a2076b9bb22a85cc2c162f20fab3898f4101` | USDC inbound (dust) | 1,000 | 0 | USDC |

29 counterparties.

## Grove — Base (`0x9b746dbc…cecee`)

| Counterparty | Label | In (USD) | Out (USD) | Tokens |
|---|---|---:|---:|---|
| `0xbeef0e0834849acc03f0089f01f4f1eeb06873c9` | BBQ/Steakhouse vault manager (MetaMorpho curator) | 20,012,318 | 87,698,686 | USDC |
| `0x0000…0000` | mint (grove-bbqUSDC, steakUSDC shares) | 87,695,389 | 0 | USDC, grove-bbqUSDC, steakUSDC |
| `0xfd78ee919681417d192449715b2594ab58f5d002` | USDC outbound (same address as on Grove ETH) | 0 | 19,007,943 | USDC |
| `0xbeef2d50b428675a1921bc6bbf4bfb9d8cf1461a` | USDC outbound | 0 | 1,000,276 | USDC |

4 counterparties.

## Grove — Avalanche (`0x7107dd8f…153644`)

| Counterparty | Label | In (USD) | Out (USD) | Tokens |
|---|---|---:|---:|---|
| `0x9a9ecae25bcb433c51edc1911be6390d397e8431` | AVAX gas funder | 1,565,670 | 0 | AVAX |
| `0xc13fda27301ef1d34cd3fc7a086a23824e94b0ed` | AVAX gas funder | 713,560 | 0 | AVAX |

2 counterparties. Only gas funding — actual AUSD venue flows happen via other tables not yet indexed.

## Grove — Monad (`0x94b398ac…cccbc`)

| Counterparty | Label | In (USD) | Out (USD) | Tokens |
|---|---|---:|---:|---|
| `0x6b405dca74897c9442d369dcf6c0ec230f7e1c7c` | AUSD/USDC routing | 29,798,559 | 29,800,386 | AUSD, USDC |
| `0x32841a8511d5c2c5b253f45668780b99139e476d` | AUSD routing | 18,604,858 | 24,795,504 | AUSD |
| `0x3b4979ec0ac20d800e434947a3516ec140de7f19` | AUSD inbound | 25,005,085 | 0 | AUSD |
| `0x0000…0000` | mint (grove-bbqAUSD, grove-steakAUSD) | 0 | 18,619,424 | AUSD, WMON, grove-bbqAUSD, grove-steakAUSD |
| `0x725ab8cad931bcb80fdbf10955a806765cce00e5` | AUSD outbound | 0 | 207,830 | AUSD |
| `0xa02318f858128c8d2048ef47171249e9b4a0deda` | AUSD inbound | 207,777 | 0 | AUSD |
| `0x0edae87c15fcbfde1f1900360830a4a4f3c438fa` | MON routing | 0 | 17,195 | MON |
| `0x3bd359c1119da7da1d913d1c4d2b7c461115433a` | MON inbound | 14,616 | 0 | MON |
| `0x3ef3d8ba38ebe18db133cec108f4d14ce00dd9ae` | WMON inbound | 12,312 | 0 | WMON |

9 counterparties.

---

## Spark — Ethereum (`0x1601843c…18347e`)

Largest counterparty set (55 rows after filter). Sorted by total_usd descending.

| Counterparty | Label | In (USD) | Out (USD) | Tokens |
|---|---|---:|---:|---|
| `0xc395d150e71378b47a1b8e9de0c1a83b75a08324` | sUSDS staking vault or similar (USDS hub) | 30,283,514,977 | 26,791,734,301 | USDS |
| `0x0000…0000` | mint/burn (29 tokens — sp*, aEth*, syrup*, spark*) | 48,467,819,847 | 8,375,789,282 | 29 tokens |
| `0x3225737a9bbb6473cb4a45b7244aca2befdb276a` | AllocatorVault dispenser | 0 | 32,749,599,610 | DAI, USDS |
| `0x37305b1cd40574e4c5ce33f8e8306be057fd7341` | LitePSM-USDC | 13,475,118,987 | 12,186,072,771 | USDC |
| `0xf6e72db5454dd049d0788e411b06cfaf16853042` | DaiUsds converter | 12,185,918,580 | 13,475,002,035 | DAI |
| `0xe7df13b8e3d6740fe17cbe928c7334243d86c92f` | USDT routing (Aave / Spark Liquidity market) | 6,044,138,866 | 6,602,437,949 | USDT |
| `0xc02ab1a5eaa8d1b114ef786d9bde108cd4364359` | USDS routing | 5,869,066,488 | 6,148,765,400 | USDS |
| `0xa3931d71877c0e7a3148cb7eb4463524fec27fbd` | USDS routing | 4,819,175,727 | 5,831,392,062 | USDS |
| `0xc4922d64a24675e16e1586e3e3aa56c06fabe907` | **unresolved — $6.24B USDC outflow** | 0 | 6,239,291,398 | USDC |
| `0x23878914efe38d27c4d67ab83ed1b93a74d4086a` | USDT routing | 2,231,091,689 | 2,230,165,864 | USDT |
| `0xe2e7a17dff93280dec073c995595155283e3c372` | USDT routing | 2,631,881,609 | 1,739,038,885 | USDT |
| `0x4dedf26112b3ec8ec46e7e31ea5e123490b05b8b` | DAI routing | 1,682,502,383 | 1,934,717,543 | DAI |
| `0x73e65dbd630f90604062f6e02fab9138e713edd9` | DAI routing | 1,790,885,360 | 1,786,342,224 | DAI |
| `0x28b3a8fb53b741a8fd78c0fb9a6b2393d896a43d` | LayerZero OFT (same address on Spark Avalanche) | 1,907,625,165 | 1,435,848,507 | USDC |
| `0x00836fe54625be242bcfa286207795405ca4fd10` | USDT/sUSDS routing | 1,551,066,991 | 1,599,756,748 | USDT, sUSDS |
| `0xc7cdcfdefc64631ed6799c95e3b110cd42f2bd22` | USDT routing | 1,430,077,735 | 1,546,835,975 | USDT |
| `0x779224df1c756b4edd899854f32a53e8c2b2ce5d` | PYUSD venue | 861,321,384 | 958,522,397 | PYUSD |
| `0xe3490297a08d6fc8da46edb7b6142e4f461b62d3` | **unresolved — $1.77B inflow** | 1,767,113,345 | 0 | USDC, USDT |
| `0x7fc7c91d556b400afa565013e3f32055a0713425` | USDe inbound (Ethena) | 1,580,711,744 | 0 | USDe |
| `0x9d39a5de30e57443bff2a8307a4256c8797a3497` | USDe outbound (Ethena) | 0 | 1,574,919,170 | USDe |
| `0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b` | Maple syrupUSDC vault (also OBEX venue) | 744,475,436 | 722,257,384 | USDC |
| `0xa632d59b9b804a956bfaa9b48af3a1b74808fc1f` | PYUSD/USDS routing | 597,915,350 | 697,860,566 | PYUSD, USDS |
| `0x377c3bd93f2a2984e1e7be6a5c22c525ed4a4815` | USDC routing | 616,864,045 | 626,695,310 | USDC |
| `0x8f0ee0393eae7fc1638bd7860a3fec6a663786ae` | **unresolved — $950M USDC outflow (7-day window Jul-2025)** | 0 | 949,973,065 | USDC |
| `0x56a76b428244a50513ec81e225a293d128fd581d` | USDC routing | 458,911,869 | 475,537,112 | USDC |
| `0xe41a0583334f0dc4e023acd0bfef3667f6fe0597` | USDS routing | 460,029,435 | 459,105,292 | USDS |
| `0x2d4d2a025b10c09bdbd794b4fce4f7ea8c7d7bb4` | **unresolved — $805M USDC outflow** | 0 | 804,529,782 | USDC |
| `0xd1917664be3fdaea377f6e8d5bf043ab5c3b1312` | **unresolved — also Grove ETH counterparty** | 0 | 800,530,363 | USDC |
| `0x356b8d89c1e1239cbbb9de4815c39a1474d5ba7d` | USDT routing | 375,599,625 | 374,998,413 | USDT |
| `0xfe6eb3b609a7c8352a241f7f3a21cea4e9209b8f` | WETH routing | 278,027,956 | 249,673,073 | WETH |
| `0x59cd1c87501baa753d0b5b5ab5d8416a45cd71db` | WETH routing (mirror of above) | 249,673,073 | 278,027,956 | WETH |
| `0x55fe002aeff02f77364de339a1292923a15844b8` | USDC inbound | 454,037,078 | 0 | USDC |
| `0x98c23e9d8f34fefb1b7bd6a91b7ff122f4e16f5c` | USDC routing | 207,786,526 | 207,666,847 | USDC |
| `0x0000000005f458fd6ba9eeb5f365d83b7da913dd` | Janus Anemoy issuer (JTRSY) | 100 | 400,256,999 | JTRSY, USDC |
| `0x383e6b4437b59fff47b619cba855ca29342a8559` | PYUSD/USDC routing | 194,590,068 | 194,649,093 | PYUSD, USDC |
| `0x09aa30b182488f769a9824f15e6ce58591da4781` | USDS routing | 182,246,764 | 181,586,518 | USDS |
| `0x774ae279c21b6a17a6e2bd5ab5398ff98f398807` | USDC outbound | 0 | 300,150,808 | USDC |
| `0xcfc0f98f30742b6d880f90155d4ebb885e55ab33` | USDC inbound (also Grove ETH counterparty) | 200,037,797 | 0 | USDC |
| `0x32a6268f9ba3642dda7892add74f1d34469a4259` | USDS routing | 88,646,574 | 88,385,906 | USDS |
| `0x49506c3aa028693458d6ee816b2ec28522946872` | **Anchorage custodial** ($150M off-chain BTC position) | 7,590,985 | 155,179,986 | USDC |
| `0xdb48ac0802f9a79145821a5430349caff6d676f7` | USDC outbound | 0 | 150,033,696 | USDC |
| `0x7ad5ffa5fdf509e30186f4609c2f6269f4b6158f` | syrupUSDC routing | 0 | 99,942,879 | syrupUSDC |
| `0x52aa899454998be5b000ad077a46bbe360f4e497` | sUSDS routing | 20,082,498 | 19,949,132 | sUSDS |
| `0x38464507e02c983f20428a6e8566693fe9e422a9` | USDC routing | 5,052,984 | 15,004,881 | USDC |
| `0xfc0539d019482d311c161ae3b756cdccdec45e87` | PayPal PYUSD feeder | 18,273,994 | 0 | PYUSD |
| `0x80128dbb9f07b93dde62a6daeadb69ed14a7d354` | PYUSD routing | 4,135,924 | 3,579,615 | PYUSD |
| `0xd0ec8cc7414f27ce85f8dece6b4a58225f273311` | USDe inbound | 7,287,349 | 0 | USDe |
| `0x4f493b7de8aac7d55f71853688b1f7c8f0243c85` | USDC/USDT routing | 2,108,732 | 2,109,545 | USDC, USDT |
| `0x6a01c16eb312b80535f4799e4bf7522b715aacff` | USDC inbound | 1,662,155 | 0 | USDC |
| `0x1e30f9c2c688f85c82111d1d262bfd127e687282` | PYUSD inbound | 1,250,101 | 0 | PYUSD |
| `0x000000000004444c5dc75cb358380d2e3de08a90` | Uniswap V4 PoolManager (likely) | 0 | 440,341 | PYUSD, USDS, USDT |
| `0xc8a3e1e0776b912047c89dc16470fd9c7ea1141d` | USDC inbound | 383,257 | 0 | USDC |
| `0x761cc954e2d968ac105f14794b25927a3b6216f4` | USDC inbound | 326,560 | 0 | USDC |
| `0xaf64555ddd61fcf7d094824dd9b4ebea165afc5b` | USDe inbound | 115,137 | 0 | USDe |
| `0xf89d7b9c864f589bbf53a82105107622b35eaa40` | USDe inbound | 63,485 | 0 | USDe |
| `0xdb80133b4fea44869e6dd1c969d38c989c2b7a07` | USDC inbound | 44,959 | 0 | USDC |
| `0xaa2461f0f0a3de5feaf3273eae16def861cf594e` | USDS inbound | 14,445 | 0 | USDS |
| `0xcd531ae9efcce479654c4926dec5f6209531ca7b` | USDC inbound | 10,544 | 0 | USDC |

## Spark — Base (`0x2917956e…009ca`)

| Counterparty | Label | In (USD) | Out (USD) | Tokens |
|---|---|---:|---:|---|
| `0x7bfa7c4f149e7415b73bdedfe609237e29cbf34a` | sparkUSDC / Spark Base venue | 7,474,927,932 | 7,457,722,448 | USDC |
| `0x4e65fe4dba92790696d040ac24aa414708f5c0ab` | Morpho Blue market (discontinued 2025-10) | 2,617,760,894 | 2,617,588,335 | USDC |
| `0x0000…0000` | mint (sparkUSDC, fsUSDS, sUSDS, aBasUSDC) | 3,890,170,780 | 0 | 6 tokens |
| `0xe45b133ddc64be80252b0e9c75a8e74ef280eed6` | USDC outbound (pre-sparkUSDC market) | 0 | 3,856,026,631 | USDC |
| `0x1601843c5e9bc251a3272907010afa41fa18347e` | **Spark Ethereum ALM** (cross-chain hub, $2.1B) | 1,042,428,629 | 1,090,330,628 | USDC, USDS, sUSDS |
| `0x2e1b01adabb8d4981863394bea23a1263cbaedfc` | MORPHO rewards claim | 1,126,437 | 1,317,193 | MORPHO, USDC |
| `0xf057afeec22e220f47ad4220871364e9e828b2e9` | MORPHO rewards inbound | 1,201,924 | 0 | MORPHO |
| `0x5400dbb270c956e8985184335a1c62aca6ce1333` | MORPHO rewards inbound | 215,593 | 0 | MORPHO |
| `0x3ef3d8ba38ebe18db133cec108f4d14ce00dd9ae` | MORPHO rewards inbound | 159,408 | 0 | MORPHO |

## Spark — Arbitrum (`0x92afd6f2…1d8709`)

| Counterparty | Label | In (USD) | Out (USD) | Tokens |
|---|---|---:|---:|---|
| `0x2b05f8e1cacc6974fd79a673a341fe1f58d27266` | Aave Arbitrum market / hub | 1,718,097,413 | 1,588,135,801 | USDC, USDS, sUSDS |
| `0xe7ed1fa7f45d05c508232aa32649d89b73b8ba48` | USDC outbound (CCTP or bridge) | 0 | 823,042,930 | USDC |
| `0x0000…0000` | mint (aArbUSDCn, fsUSDS, sUSDS) | 698,427,450 | 0 | 5 tokens |
| `0x724dc807b04555b71ed48a6896b6f41593b8c637` | USDC routing | 211,158,026 | 211,146,861 | USDC |

## Spark — Optimism (`0x876664f0…f36fb`)

| Counterparty | Label | In (USD) | Out (USD) | Tokens |
|---|---|---:|---:|---|
| `0xe0f9978b907853f354d79188a3defbd41978af62` | Aave Optimism market / hub | 605,103,521 | 604,932,561 | USDC, USDS, sUSDS |
| `0x0000…0000` | mint | 479,757,093 | 0 | USDC, USDS, sUSDS |
| `0x33e76c5c31cb928dc6fe6487ab3b2c0769b1a1e3` | USDC outbound | 0 | 474,899,482 | USDC |

## Spark — Unichain (`0x345e368f…f5869`)

| Counterparty | Label | In (USD) | Out (USD) | Tokens |
|---|---|---:|---:|---|
| `0x7b42ed932f26509465f7ce3faf76ffce1275312f` | Unichain USDC hub (likely sparkUSDC-uni) | 1,137,431,206 | 1,147,660,373 | USDC, USDS, sUSDS |
| `0x0000…0000` | mint | 457,681,021 | 0 | USDC, USDS, sUSDS |
| `0x726bfef3cbb3f8af7d8cb141e78f86ae43c34163` | USDC outbound | 0 | 442,489,003 | USDC |

## Spark — Avalanche (`0xece6b0e8…b7fec`)

| Counterparty | Label | In (USD) | Out (USD) | Tokens |
|---|---|---:|---:|---|
| `0x28b3a8fb53b741a8fd78c0fb9a6b2393d896a43d` | LayerZero OFT (same on Spark ETH) | 686,613,444 | 645,614,216 | USDC |
| `0x420f5035fd5dc62a167e7e7f08b604335ae272b8` | USDC outbound | 0 | 754,408,589 | USDC |
| `0x0000…0000` | mint (aAvaUSDC) | 713,240,976 | 0 | USDC, aAvaUSDC |
| `0x625e7708f30ca75bfd92586e17077590c60eb4cd` | USDC routing | 53,287,584 | 53,195,868 | USDC |
| `0x2e1b01adabb8d4981863394bea23a1263cbaedfc` | MORPHO rewards claim (same as Spark Base) | 76,615 | 0 | USDC |

---

## Unresolved high-value counterparties

Five distinct addresses with >$500M flow each that are not obviously venue-labeled. These are the highest-priority targets for follow-up investigation:

| Address | Prime/chain | Direction | Total (USD) | First seen | Last seen | Hypothesis |
|---|---|---|---:|---|---|---|
| `0xc4922d64a24675e16e1586e3e3aa56c06fabe907` | Spark ETH | outbound only | 6,239,291,398 | 2024-11-18 | 2026-04-22 | Large persistent USDC sink — possibly Circle / CCTP mint burner, or an off-chain custodial |
| `0xe3490297a08d6fc8da46edb7b6142e4f461b62d3` | Spark ETH | inbound only | 1,767,113,345 | 2025-02-21 | 2025-10-21 | One-way USDC+USDT inflow over 8 months, then stops — possible closed market position unwind |
| `0x8f0ee0393eae7fc1638bd7860a3fec6a663786ae` | Spark ETH | outbound only | 949,973,065 | 2025-07-17 | 2025-07-25 | $950M moved in 8-day window — looks like a one-shot rebalance or venue-deployment |
| `0x2d4d2a025b10c09bdbd794b4fce4f7ea8c7d7bb4` | Spark ETH | outbound only | 804,529,782 | 2025-01-13 | 2025-06-02 | USDC outflow, 172 transfers, stopped Jun-2025 |
| `0xd1917664be3fdaea377f6e8d5bf043ab5c3b1312` | Spark ETH + Grove ETH | outbound only | 800,530,363 (Spark) + 800,087,454 (Grove) | 2025-04-07 | 2026-04-13 | **Cross-prime — same address is a USDC outbound sink for both primes.** Highest-confidence candidate for a shared infra contract |

## Cross-prime / cross-chain discoveries

1. **Grove ETH ↔ Spark ETH** — Grove's Ethereum ALM ended up holding BUIDL-I and JTRSY tokens that were originally delivered to Spark Ethereum's ALM (see `0x0000000005f458fd6ba9eeb5f365d83b7da913dd` Janus issuer appearing on both). Suggests Grove bootstrapped part of its book from Spark's positions rather than fresh issuance.
2. **Spark ETH ↔ Spark Base** — `0x1601843c…18347e` (Spark Ethereum ALM itself) appears as a $2.1B counterparty on Spark Base ALM. Cross-chain hub pattern — USDS/sUSDS bridged from ETH mainnet to Base.
3. **Spark ETH ↔ Spark Avalanche** — `0x28b3a8fb53b741a8fd78c0fb9a6b2393d896a43d` is a counterparty on both ($3.3B on ETH, $1.3B on Avax). LayerZero OFT endpoint.
4. **`0xd1917664be3fdaea377f6e8d5bf043ab5c3b1312`** — appears as >$500M USDC outbound sink on both Grove ETH and Spark ETH. Unidentified but clearly shared infra.
5. **`0xfd78ee919681417d192449715b2594ab58f5d002`** — USDC outbound on both Grove ETH ($88M) and Grove Base ($19M). Likely a Grove-specific routing contract.
6. **`0x2e1b01adabb8d4981863394bea23a1263cbaedfc`** — appears on Spark Base and Spark Avalanche as a MORPHO-rewards-related counterparty. Likely the Morpho Universal Rewards Distributor.
7. **PayPal → Spark PYUSD** ($677M aggregate) arrived mostly via mint from `0x0000…0000` (PYUSD issuance) and a small `0xfc0539d0…45e87` feeder — not via a direct EOA transfer from PayPal. Consistent with PYUSD issuance mechanics (Paxos mints on demand).

## Methodology

Query logic (simplified):

```sql
-- For each ALM in scope, collect every transfer in/out of it
-- Group by (prime, chain, alm, counterparty) and sum directional flow
-- Filter total_usd < $1k to drop airdrop spam
```

Full SQL in Dune query [7357558](https://dune.com/queries/7357558). Uses `tokens.transfers` (cross-chain spell, partitioned by `block_date`). Execution cost: ~104 credits at medium performance.

## Limitations

- **Plume missing.** Grove Plume ALM (`0x1db91ad5…76f812`) returns zero rows — `tokens.transfers` does not cover Plume yet. See [grove/QUESTIONS.md](grove/QUESTIONS.md) §Q4.
- **Labels are best-effort.** Address labelling is cross-referenced against stars-api allocation addresses and known Sky contracts. Unresolved counterparties need eth_call / contract-source inspection to confirm.
- **Spam filter is a blunt instrument.** `$1k` cutoff may hide legitimate dust flows (gas top-ups, initial deposits). The Avalanche gas-funder entries are just above the threshold.
- **Snapshot-in-time.** This file is a point-in-time dump. Regenerate by re-executing query 7357558 when needed.
