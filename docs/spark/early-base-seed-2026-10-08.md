# Spark's initial Base funding — November 2024 to January 2025

Three governance draws fund four authenticated Base deliveries totaling
**198,000,000 USDS of borrowed acquisition cost**. The Ethereum cash passed
through Spark's subproxy and the bridge escrow, bypassing the Ethereum ALM.
The normalized history observes the ilk draws and the Base receipts but did
not previously connect their funding.

| Spell | Execution (UTC) | Borrowed cost | Base asset received |
|---|---|---:|---|
| [November 14, 2024](https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20241114/SparkEthereum_20241114.sol) | November 18 | 1,000,000 | USDS |
| Same spell | November 18 | 8,000,000 | sUSDS bought with 8m USDS |
| [November 28, 2024](https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20241128/SparkEthereum_20241128.sol) | November 30 | 90,000,000 | sUSDS bought with 90m USDS |
| [January 9, 2025](https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20250109/SparkEthereum_20250109.sol) | January 13 | 99,000,000 | USDS |

Evidence is losslessly compressed in
`tests/fixtures/spark_early_base_seed.json.gz`: full source transaction logs,
reconstructed relay identifiers, and the Base transaction logs. Tests verify:

- Actual allocator-buffer USDS payments to the subproxy and USDS deposited
  into sUSDS; the cost does not come from the value of later received shares.
- Actual subproxy payments into the L1 escrow `0x7f311a4d…bdbe9ef3`.
- Base's canonical messenger `0x866e82a6…d58b0afa` relaying the Sky token bridge
  `0xa5874756…7ced352a` to its Base counterpart `0xee44cdb6…d7b8a7b7`.
  The messenger, token bridge and asset escrow are distinct contracts.
- Exact relay payload hashes emitted by the Base messenger, and the token
  mint's asset, destination and raw amount matching each bridge payload.

The recipient is Base ALM `0x2917956eff0b5eaf030abdb4ef4296df775009ca`.
Pending bridge claims preserve the original Sky-funded cost. The two sUSDS
receipts were worth approximately 3.73 and 46.62 USDS more than their
acquisition costs at arrival; that savings growth does not become new debt.
No idle-capital exemption is added for in-flight claims.

The adapter uses only these exact reviewed transactions. It rejects source
custody already accounted for, wrong ilk/draw, changed receipt evidence, and
mixed raw/already-linked input. Missing destination events leave a funded
pending claim; missing source funding leaves a receipt unresolved.

These historical capital links do not restate revenue, change global debt,
or alter published borrowing charges. Isolated route tests establish the
funding links, not full Spark reconciliation. Savings V2 funding and other
unresolved routes still require separate treatment.
