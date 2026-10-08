# The June 2025 400m draw funded Optimism and Unichain via the subproxy

The May 29, 2025 Spark Ethereum spell, executed June 2, drew **400m USDS**
against the Spark ilk, pulled it from the allocator buffer into the subproxy,
wrapped 200m into sUSDS, then bridged 100m USDS and 100m-cost sUSDS to each of
the Optimism and Unichain ALMs. None of this cash passed through Ethereum's
ALM. The old replay therefore recorded a 400m draw with no matching investment,
then four apparently unfunded receipts on the destination chains.

Exact spell source:
https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20250529/SparkEthereum_20250529.sol

Source execution:
https://etherscan.io/tx/0x4ddc25f122e8092d40a008e2a1807ed81beb514f054345085ef863cdf266cf5e

The canonical fixture contains the 400m allocator-buffer→subproxy transfer,
the 200m USDS→sUSDS deposit, and all four exact subproxy→bridge-escrow payments.
For each `SentMessage`, the test reconstructs the full version-1 relay calldata
including nonce, sender, target, zero message value, gas limit and payload.
Its hash matches the destination messenger's successful `RelayedMessage`,
and the actual destination mint matches the encoded token, recipient and units.
No date/amount-only association is used.

The four source allocations each carry **100m borrowed principal**. The received
sUSDS marks are 100,000,011.724437 on Optimism and 100,000,006.699678 on Unichain;
the increase does not create additional borrowed basis. Claims retain funded
custody until the observed receipt. Future or otherwise unidentified receipts
cannot manufacture an opening funded position from these historical facts.

The adapter requires the exact source draw, funding ilk, source metadata and
receipt shapes. If another normalizer starts recording source custody, it fails
rather than counting both that custody and the new claims. It is idempotent.
The minute-scale bridge claims are separate tracing-only rows, with no revenue,
APY, or idle-USDS exemption.

Regression tests verify the independent contract evidence and full 400m basis
conservation, as well as missing-destination cutoffs and conflicting source
custody/ilk rejection. The full Spark replay will measure the downstream
financing effect; the source routing identification alone is not a claim that
Spark now reconciles. Settlement debt, reported revenue and API are unchanged.
