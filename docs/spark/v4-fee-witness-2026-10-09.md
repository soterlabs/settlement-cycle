# Spark S61: independently prove a V4 fee receipt

The August 20, 2026 [transaction](https://etherscan.io/tx/0x18a9cbdbd1fcf140e5e21b72571c1455d13be6ca5256c8e9f122f43873824593)
leaves a **12,667.033418674457 USD** incoming discrepancy in the frozen capital
replay. Its S61 Uniswap V4 NFT, token ID 324005, both withdraws liquidity and
collects earned fees. The normalizer tracks its principal withdrawal but
explicitly leaves V4 fee amounts unclassified.

Independent position state now proves:

| Component | Amount at par |
|---|---:|
| Earned PYUSD fees | 6,311.595421 |
| Earned USDS fees | 6,355.428945075564808842 |
| Total earned fees | **12,667.024366075564808842** |
| Returned liquidity principal | 1,215.925898327927459078 |
| Remaining incoming discrepancy after explaining fees | **0.00905259889230006504868** |

The returned principal exactly matches the normalized NFT withdrawal. It is
not an assumed fee calculated to make the transaction balance.

## Independent method

At pinned Uniswap core commit
[`46c6834`](https://github.com/Uniswap/v4-core/blob/46c6834698c48bc4a463a86d8420f4eb1d7f3b75/src/libraries/Position.sol),
`Position.update` calculates fees from the previous liquidity and the change in
last-recorded fee growth, using integer multiplication and division by Q128.
The [storage reader](https://github.com/Uniswap/v4-core/blob/46c6834698c48bc4a463a86d8420f4eb1d7f3b75/src/libraries/StateLibrary.sol)
provides the mapping layout. We read the three position words at blocks
25,796,189 and 25,796,190 and independently verify liquidity through the NFT
manager.

The entire block's modification logs show exactly one modification of this
position. Thus the post-block last-recorded growth is the checkpoint written by
this specific operation, even if other pool swaps occur in the block. The proof
checks the pool key, ticks, zero hook address, NFT owner, signed liquidity
change, and actual token transfers to the ALM. It refuses multiple modifications
of the position in the block. It does not require a debug trace endpoint.

## Scope and reproduction

This is a read-only economic explanation. The two running funded replays remain
frozen and still contain this unclassified receipt. No API or settlement revenue
is changed, and these historical fees are not an August borrowing-cost saving.
A future normalizer update can use this method for supported single-modification
blocks; multiple modifications and hooks need their own handling.

```sh
PYTHONPATH=src PYTHON_DOTENV_DISABLED=1 .venv/bin/python \
  scripts/audit_spark_v4_fee_witness.py \
  --evidence tests/fixtures/spark_v4_fee_witness.json.gz \
  --output /tmp/spark-v4-fee-witness.json
```

The committed result is `reconciliation/spark_v4_fee_witness_2026_08.json`.
Five tests cover actual fees and principal, independence from the reported
residual, a second modification in the block, wrong storage/liquidity reads,
missing cash, and an inconsistent normalized principal leg.
