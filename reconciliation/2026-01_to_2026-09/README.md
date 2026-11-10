# January–September 2026 MSC reconciliation (work in progress)

Comparison baseline: `536f1492089ed82fbf6eaabb956f7a194b887d47`.
The current checkpoint reviews merged changes through
`9b290a3f164882f2e19cad9fbc7ad597bce400a7`.

The calculation and forum post remain in progress. Before finalizing them,
implement the separate Spark/Obex async-vault PRD in #263 using #226's framework,
calculate its historical impacts, complete two review/fix rounds and merge it.
Then rebase this reconciliation branch onto main and include those results.

Existing evidence:

- `pr-inventory.json`: classifications for the 47 merged PRs in the current range.
- `venue-evidence.json.gz`: pinned public RPC responses, completed log queries,
  certified daily boundaries and production component calculations. Source
  credential URLs are excluded. This is a component audit, not a new monthly
  all-token/all-chain transfer-review attestation.
- `rlusd-receipts.json`: six native RLUSD partner payments totaling 1,373,208,
  all received in September. The scan found no earlier payments from this sender.
- `dr-code-evidence.json`: historical source check for Osero codes 3014/3017.

`python -c "import gzip,json; from scripts.replay_reconciliation_venues import replay; print(replay(json.loads(gzip.decompress(open('reconciliation/2026-01_to_2026-09/venue-evidence.json.gz','rb').read()))))"`
replays Spark S70/S72 September and Grove E44 July–September without network.
It uses existing production capital and revenue primitives, including event-day
NAV and native lending yield plus authenticated external spToken receipts.

Verified new measurements at this checkpoint:

| Prime | Item | Earning month | USDS-equivalent income |
| --- | --- | --- | ---: |
| Spark | Sentora RLUSD S72 | September | 3,674.16 |
| Spark | SparkLend RLUSD S70, including 113.73 reserve receipts | September | 2,400.00 |
| Grove | Diamond PAU Uniswap E44 | August | 27.90 |
| Grove | Diamond PAU Uniswap E44 | September | 1,002.55 |

The separate existing CoF, BUIDL and Osero replays have also been reproduced.
No grand total or new execution instruction is finalized here. Existing October
credits must be included once, and October partner receipts remain ordinary
October income. Original published settlements, database results, API revisions
and settlement configuration have not been changed.
