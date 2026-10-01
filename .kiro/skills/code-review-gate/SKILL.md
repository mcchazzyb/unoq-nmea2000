---
name: code-review-gate
description: Standard change workflow with a different-model AI review gate before commit — ticket, branch, author, cross-model review (block on Critical, advise on High), commit, push, PR, CI.
triggers: code review gate, review before commit, review my changes, pre-commit review, change workflow, how we work on tickets, reviewer model, two-model review
inclusion: always
---

# Code Review Gate — the standard change workflow

This is the mandatory workflow for ANY code change in this repository. It puts a
second AI model between "the code is written" and "the code is committed", so a
reviewer with no author bias catches real problems before they reach the
pipeline. It is a **project skill**: it lives in the repo (`.kiro/skills/`), so
copying this folder into another project, or merging this repo, makes that
project adhere to the same gate.

Written for a solo operator who is not a day-to-day developer: the goal is to
avoid shipping failures and raise code quality without manual code reading.

## Why two models

The model that WROTE a change is a poor judge of it — it already believes the
change is correct, so it rubber-stamps. A **different** model, given the diff and
full context but none of the author's "I just wrote this" bias, catches more real
defects. This is standard practice on serious teams (GitHub Copilot review,
CodeRabbit, Graphite Reviewer all do model-based PR review). Here:

- **Author model:** `claude-sonnet-5` — writes and edits the code (the main session).
- **Reviewer model:** `gpt-5.6-terra` — reviews the staged diff with full context.
  (Verified available via `kiro-cli chat --list-models`. It is the premium model
  — rate 4.4× — so ALWAYS scope the review to the diff + the specific files it
  touches, never the whole repo, or it is slow and expensive.)

If `gpt-5.6-terra` is ever unavailable, fall back to `claude-opus-5.5` — any capable model that is NOT the author model. Never let the author model review its own change.

## The severity contract (this is what controls nitpicking)

The reviewer classifies every finding and the gate acts on severity:

| Severity | Meaning | Gate action |
|---|---|---|
| **Critical** | Will break production: data loss, broken payment/webhook, security hole, PII leak, a crash on a normal path, a schema/encryption mismatch. | **BLOCK the commit.** Fix it or, if the reviewer is wrong, challenge it in writing and record why before overriding. |
| **High** | Likely bug or real risk, but not guaranteed breakage: a missed edge case, a race, a missing error path, a test gap on changed logic. | **ADVISE.** Report it, fix it if the fix is clear, otherwise note it and proceed — the human decides. |
| Medium / Low | Style, naming, "consider", refactors, formatting, preferences. | **DISCARD. Never reported.** |

The reviewer is explicitly instructed: report ONLY Critical and High, name the
concrete failure mode for each (what breaks, when), and if it cannot name a real
failure it stays silent. No formatting, no naming, no "you could also", no
nitpicks. This repo's own history of real failures (see `.kiro/steering/`) is the
bar: cross-region KMS gaps, raw-vs-JSON secret shape, DynamoDB reserved keywords,
E.164 lookup parity, cert mismatches — those are Critical. Tabs vs spaces is noise.

## The workflow (follow in order)

```
work the ticket (bd)
  → create a local branch off develop
  → author the change (claude-sonnet-5) + build/typecheck it green
  → stage the diff
  → REVIEW GATE  (gpt-5.6-sol, fresh context, reviews the staged diff)
      ├─ Critical  → BLOCK: fix or challenge-in-writing, then re-review
      ├─ High      → fix if clear, else note and proceed
      └─ clean     → proceed
  → commit (separate git calls, no && chaining)
  → push the branch by name
  → open PR to develop
  → CI pipeline runs
```

### Step 1 — Ticket
Claim the beads ticket: `bd ready`, `bd show <id>`, `bd update <id> --claim`.
If there is no ticket for the work, create one first (`bd create`).

### Step 2 — Branch
Create a local feature branch off `develop`:
`git checkout develop` → `git pull --rebase origin develop` → `git checkout -b feature/<short-name>`.
Each git command is a SEPARATE call — never chain with `&&` (the approval flow
denies chained git).

### Step 3 — Author + build
Make the change. Then run the project build/typecheck and read the exit status
explicitly BEFORE staging — a red build never reaches the review gate.
Frontend: `npm run build` in `packages/frontend`. Confirm `exit 0`.

### Step 4 — Stage the diff
`git add <specific files>` (name the files; never `git add .`). `git status --short`
to confirm exactly the intended files are staged.

### Step 5 — THE REVIEW GATE
Spawn the reviewer as a subagent pinned to the reviewer model, with fresh eyes:

```
spawn_run(
  model="gpt-5.6-sol",
  include_memory=false,     # no author bias — reviewer must not inherit "I wrote this"
  include_lessons=true,     # keep the repo's learned-failure corrections
  include_project=true,     # it needs to open the files the diff touches
  task=<the reviewer prompt below, with the diff and ticket pasted in>
)
```

Reviewer prompt (paste the staged diff and the ticket summary into it):

> You are a senior reviewer. Review ONLY the staged diff below against the ticket
> and the project's steering docs in `.kiro/steering/`. Classify each finding as
> Critical, High, Medium or Low.
> - Critical = will break production (data loss, broken payment/webhook, security,
>   PII leak, a crash on a normal path, a schema/encryption/format mismatch).
> - High = a likely bug or real risk but not guaranteed breakage.
> - Report ONLY Critical and High. For each, name the exact failure: what breaks
>   and when. If you cannot name a concrete failure, do not report it.
> - DO NOT report style, naming, formatting, "consider", or refactor suggestions.
> - If the diff is clean, say exactly: "No Critical or High issues."
> Diff:
> <staged diff>
> Ticket: <summary>

### Step 6 — Act on the verdict
- **Any Critical** → do NOT commit. Fix it and re-run the gate from Step 3, OR, if
  the reviewer is genuinely wrong, write one line stating why it is wrong and only
  then override. An unexplained override of a Critical is not allowed.
- **High** → fix it if the fix is clear and small; otherwise record it (in the PR
  description or a follow-up ticket) and proceed.
- **Clean / only Medium-Low** → proceed to commit.

### Step 7 — Commit
Separate git calls: `git commit -m '<short message>'` (subject ≤72 chars). For a
message with quotes/`$`/backticks, write `/tmp/commit-msg.txt` first and
`git commit -F`.

### Step 8 — Push + PR
`git push origin feature/<short-name>` (name the branch; never a bare `git push`).
Open the PR to `develop` with `gh pr create`. Put the review verdict and any
accepted-High notes in the PR description. CI then runs.

## Gotchas

- **Never let the author model review its own change** — the whole point is a
  different model. If you authored in Sonnet, review in gpt-5.6-sol.
- **Scope the review to the diff**, not the repo — gpt-5.6-sol is 4.4× rate.
- **The gate runs on changes that go through an agent session.** A hand-edit that
  bypasses the session also bypasses the gate — that is the known limit of this
  in-session approach. (A GitHub Action reviewer would close that gap for every
  PR; not built here yet.)
- **Block only on Critical.** If every High blocked, the gate would stall on
  judgement calls and you would start ignoring it. Advise on High, block on
  Critical — that keeps the gate trusted.
- **Verify the model IDs** with `kiro-cli chat --list-models --format json`
  before relying on them; these are experimental-preview models and IDs change.
