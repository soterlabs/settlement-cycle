-- Labels for unresolved ALM USDC senders. Ad-hoc.
SELECT address, blockchain, name, category
FROM labels.addresses
WHERE blockchain = 'ethereum'
  AND address IN (
      0x9dd1929124a9ad8d1bc7f029eebbbfeb0d898318,
      0x92e75576f81838df5d019940e740117f57924e9b,
      0xcfc0f98f30742b6d880f90155d4ebb885e55ab33,
      0x040170aa9aaa916c2e8135777a31f17c440ba52a,
      0xb52845f26bb7a4bf0638ab778e220b56565066d2,
      0x748b66a6b3666311f370218bc2819c0bee13677e
  )
  AND CAST({{pin_block}} AS bigint) >= 0
