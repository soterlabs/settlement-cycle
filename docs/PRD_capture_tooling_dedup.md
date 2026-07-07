# PRD — Deduplicate pin blocks & capture tooling

**Status:** proposed (2026-07-07)
**Trigger:** code-review findings #6/#7/#8 on `feat/run-june` (PR #144). All
three are the same disease — *one fact written in several places, kept in
sync only by human discipline*. Correct today; each new month adds another
chance to diverge silently.
**Scope:** pure refactor. No methodology change, no report-number change.

## 1. The three duplications

**(a) Month-end pin blocks are hand-copied across files** *(finding #6)*.
The block meaning "last block ≤ month-end 23:59:59 UTC" per chain lives in:

- `scripts/run_spark_2026.py::PIN_BLOCKS_BY_MONTH`
- `scripts/run_grove_2026.py::PIN_BLOCKS_BY_MONTH`
- `tests/fixtures/grove_2026_MM/_capture_dune_fixtures.py::PIN_BLOCKS_{SOM,EOM}`
  (one copy per month)

Shared chains (ethereum/base/avalanche_c) must agree everywhere; nothing
checks that they do. A typo in one file → Spark and Grove measure "end of
month" at different moments, with no error. (This nearly happened in June:
the eth EoM pin appears in 3 tracked files + 2 capture scripts.)

**(b) The Grove capture script is copy-pasted per month** *(finding #7)*.
`tests/fixtures/grove_2026_{04,05,06}/_capture_dune_fixtures.py` are ~300-line
near-identical copies differing only in pins, dates, and output paths. Bug
fixes don't propagate: the June copy bypasses the poisoned extract cache
(`SETTLE_NO_CACHE=1` lesson, 2026-07-03) and fixes the wrong
`START_DATE` — April's and May's copies would happily re-capture bad data.

**(c) Safety pin blocks are defined twice** *(finding #8)*.
`scripts/extend_spark_fixtures.py::SAFETY_PIN_BLOCK` and
`scripts/capture_spark_inflow_by_counterparty.py::JUNE_30_PIN_BLOCK` carry
the same hand-estimated "a-bit-after-month-end" upper bounds. Both must be
bumped every month; they are also derivable, so they shouldn't exist as
data at all.

## 2. Proposal

### 2.1 One pins file (fixes a, c)

New `config/pin_blocks.yaml` — the single source of truth:

```yaml
# EoM = last block ≤ <last day of month> 23:59:59 UTC, resolved via
# blocks_at_eod (Dune 7474490). SoM(month N) ≡ EoM(month N−1) — derived,
# never stored. Verified values only; add a month by appending one block.
2026-06:
  ethereum:    25433938
  base:        48037326
  arbitrum:   479089705
  optimism:   153632611
  unichain:    52115640
  avalanche_c: 89166730
  plume:       78267500
  monad:       84784216
2026-05:
  ...
```

- `src/settle/domain/pins.py` (~30 lines): `eom(year, month) -> dict[Chain, int]`,
  `som(year, month)` (= EoM of the previous month; raises if missing),
  `safety_pin(chain, year, month)` (= EoM + per-chain `blocks/day × 7` buffer
  — replaces both hand-maintained safety dicts).
- Consumers: both runners' `PIN_BLOCKS_BY_MONTH` dicts are deleted and read
  from the loader; `extend_spark_fixtures.py`,
  `capture_spark_inflow_by_counterparty.py`, and the unified Grove capture
  (§2.2) take pins from the same loader.
- Guard: a unit test asserts (i) every month present is contiguous with its
  predecessor, (ii) EoM strictly increases per chain — the cheap invariants
  a hand-edit can break.

### 2.2 One parameterized Grove capture script (fixes b)

`scripts/capture_grove_fixtures.py --month 2026-07`:

- Pins from §2.1; output to `tests/fixtures/grove_<YYYY_MM>/` (per-month
  *data* dirs stay; per-month *code* dies).
- Sets `SETTLE_NO_CACHE=1` internally — captures must never read the extract
  cache (the June cache-poisoning incident becomes structurally impossible).
- The per-venue query inventory (currently inlined per copy) moves to one
  table in the script; venue additions edit one place.
- `tests/fixtures/grove_2026_{04,05,06}/_capture_dune_fixtures.py` are
  deleted; a note in each fixture dir's `_about` records the capture command
  that produced it.

Spark's `extend_spark_fixtures.py` already is the parameterized shape
(FIXTURE_END_DATE + refresh functions); it only swaps its pin constants for
the §2.1 loader.

## 3. Non-goals

- No change to what is captured, how values are computed, or any report
  number. Acceptance is byte-identical `summary.md` on re-run (xlsx differs
  only by generation timestamp).
- No auto-resolution of new EoM pins (keep pin entry a deliberate,
  reviewed act — it defines the settlement boundary).
- Monad Q1 placeholder pins (1/2/3/4) stay as data in the yaml, with their
  existing comment.

## 4. Acceptance criteria

1. `grep -rn "25433938" scripts/ tests/fixtures/*/_capture*.py` → only
   `config/pin_blocks.yaml` (one hit repo-wide per pin).
2. `run_spark_2026.py` + `run_grove_2026.py` Jan–Jun reproduce current
   committed `summary.md` byte-identically.
3. One Grove capture script; zero per-month script copies.
4. Zero hand-maintained safety-pin dicts.
5. Pins-file invariant test in `tests/unit/`.

## 5. Sizing / order

Small, mechanical, low-risk (pure indirection). Suggested order: pins file +
loader + runner swap (verifiable immediately via re-run) → safety-pin
derivation → Grove capture unification (verifiable at the July capture).
~1 PR, reviewable in one sitting. July 2026 settlement is the natural
forcing function: do this *before* adding 2026-07 pins so the new month is
added the new way.
