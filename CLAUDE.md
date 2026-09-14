# MSC settlement-cycle — collaboration notes for Claude

This file is auto-loaded by Claude Code when working in this repo. Keep
it short and load-bearing.

## Open-questions architecture

**One artifact: `QUESTIONS.md` at the repo root.** It owns both the content
of a question and its lifecycle. There is no GitHub-issues mirror — it was
retired in 2026-09 along with its reconciler (`scripts/sync_issues.sh`), so
a question's state is whatever the markdown says, reviewed in a PR like any
other change.

## Editing invariants

1. **Everything about a question lives in `QUESTIONS.md`.** Content and
   lifecycle both.
2. **Resolving = moving the entry** from its priority subsection to
   `## Resolved`, in the same commit that records the takeaway.
3. **Never renumber Q-IDs.** Reorder by moving an entry between
   priority subsections; the ID stays. IDs are cross-referenced from
   `PRD.md §17`, so a reused number silently re-points a citation.
4. **Trivial code edits don't trigger any of this.** Only changes that
   move methodology, accounting numbers, or counterparty-facing claims.

## The two flows

**Flow A — adding a new question:**

1. Edit `QUESTIONS.md`: pick the next free Q-ID for the counterparty
   (`G`/`S`/`B` + next free number); place under the right priority
   subsection.
2. Stage + commit.

**Flow B — a question was resolved:**

1. Move its entry to `## Resolved` in `QUESTIONS.md`, leaving a compact
   pointer (one or two lines saying what the answer was).
2. Add the methodology takeaway to **`PRD.md §17.13`** (review-acks).
3. Stage + commit — both edits together, so the resolution and its
   consequence are one reviewable change.

## Question priority scheme

- **P0** — material numerical gap in current settlement output
- **P1** — methodology unknown that would shift numbers if confirmed
- **P2** — sanity check / confirmation, no current numerical impact
- **P3** — future-proofing, operational, or dormant (venue holds $0)
