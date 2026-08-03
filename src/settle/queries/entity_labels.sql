-- Dune entity labels for the subproxy-funder cluster.
-- Ad-hoc probe for the Grove subproxy / FalconX investigation.
SELECT
    address,
    blockchain,
    name,
    category
FROM labels.addresses
WHERE blockchain = 'ethereum'
  AND address IN (
      0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43,  -- contract funding 0x4c2c0f
      0x4c2c0f0bb2631b02ac9299c59690914ee7a200b8,  -- subproxy sender (April)
      0x1157a2076b9bb22a85cc2c162f20fab3898f4101,  -- relay hop (8)
      0xc3160202af27d3f8fe12f34f011a0a7f55c60172,  -- doc addr (13)
      0x260b364fe0d3d37e6fd3cda0fa50926a06c54cea,  -- FalconX-hypothesized (10)
      0xa79cb8421443f6480527d9cd591b9ce40e716afc,  -- hub-8 funder
      0x167dd11df8494f78661bcf3cc9deed1a21631c03,  -- hub-8 funder
      0x5a03e9355cdb5f74897fc6f91b0de01868791964,  -- hub-8 funder ($7.28M)
      0xa7933f3115a5847f552f44b0e0ff9ee28286a7fc,  -- hub-8 funder
      0x9cacc22db054de0113c65fe2b0745a40436b3c12,  -- hub-8 funder
      0xac1ee1f2c8e35cb99a7823d09595db103e67b719,  -- hub-8 funder
      0x85051c8c3f2802a7a6c0abfdd410c83c56b47df2,  -- hub-8 funder
      0x43fed72b921af413aad831cebd221697b18da54f,  -- hub-8 funder
      0xc5188288fb7cbd73e3ad8741baa631cb940b0080,  -- hub-8 funder
      0xa188eec8f81263234da3622a406892f3d630f98c   -- subproxy USDC sweep destination
  )
  AND CAST({{pin_block}} AS bigint) >= 0
