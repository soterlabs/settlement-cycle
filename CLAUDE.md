# MSC settlement-cycle — collaboration notes for Claude

This file is auto-loaded by Claude Code when working in this repo. Keep
it short and load-bearing.

## Open-questions architecture

Two artifacts, both in this repo:

| Artifact | Owns | Edited by |
|---|---|---|
| `QUESTIONS.md` (repo root) | question CONTENT (title, body, counterparty, priority) | Claude / humans, in markdown |
| GitHub issues in `soterlabs/settlement-cycle` | LIFECYCLE (open ↔ closed) and triage discussion (comments) | humans, in the GitHub UI |

Reconciler: `scripts/sync_issues.sh`.

- `--check` (default) — read-only, exits 1 on drift
- `--apply` — reconciles both directions

## Editing invariants

1. **Question content goes in `QUESTIONS.md`, never in the issue UI.**
2. **Lifecycle changes happen on GitHub, not in markdown.** The sync
   script moves resolved entries to `## Resolved`; don't do it manually.
3. **Never renumber Q-IDs.** Reorder by moving an entry between
   priority subsections; the ID stays.
4. **Trivial code edits don't trigger any of this.** Only changes that
   move methodology, accounting numbers, or counterparty-facing claims.

## The two flows

**Flow A — adding a new question:**

1. Edit `QUESTIONS.md`: pick the next free Q-ID for the counterparty
   (`G`/`S`/`B` + next free number); place under the right priority
   subsection.
2. Run `./scripts/sync_issues.sh --apply` to create the GitHub issue.
3. Stage + commit.

**Flow B — a question was resolved (human closed the GitHub issue):**

1. Run `./scripts/sync_issues.sh --apply`. The script moves the entry
   from its open section to `## Resolved` in `QUESTIONS.md`.
2. Add the methodology takeaway to **`PRD.md §17.13`** (review-acks).
3. Stage + commit.

The sync-apply output prints a TODO line listing Q-IDs that just
resolved — treat that as a prompt to update `PRD.md §17.13`.

## Issue references in PRs

Same-repo references: `closes #17`, `fixes #6`. Auto-close on merge.

## Question priority scheme

- **P0** — material numerical gap in current settlement output
- **P1** — methodology unknown that would shift numbers if confirmed
- **P2** — sanity check / confirmation, no current numerical impact
- **P3** — future-proofing, operational, or dormant (venue holds $0)
