---
name: cubic-loop
description: >
  Iteratively improves code by running cubic AI reviews, fixing issues, and re-reviewing.
  Two modes with different caps: local mode (before the PR exists) runs AT MOST two
  iterations, then stops and reports the remainder; PR mode (after the PR
  is opened) loops on cubic's PR comments until none remain. Use when the user wants to
  polish their changes, says "loop until clean", "keep reviewing", "cubic loop", or as the
  steps before and after opening a PR.
allowed-tools: [Bash, Read, Grep, Glob, Edit, Write, Task]
---

# Cubic Loop

Iteratively review and fix code: review -> fix -> re-review -> repeat. How
long it repeats depends on the mode — see **Where this sits in a PR's life**.

## When To Activate

- Before opening a PR (**local mode**) and right after opening it (**PR
  mode**).
- User says "loop until clean" or "keep reviewing until done" (PR mode — the
  local loop stops at two regardless), or "cubic loop"
- User wants to fully optimize a PR or branch before merging
- User asks to "fix everything cubic finds" or "make cubic happy"

## Where this sits in a PR's life

The loop runs **twice** per PR, with different stopping rules:

| Step | Mode | Stops when | Cap |
|---|---|---|---|
| Before `gh pr create` | `local` | clean, no progress, **or 2 iterations — whichever first** | **2, hard.** Never a third local iteration, whatever remains. |
| After `gh pr create` | `pr` | **zero unresolved cubic comments** (P3-only counts as clean). No-progress and cap exits **never leave threads open**: every remaining thread gets a won't-fix reply and is resolved, or the agent reports and asks | 5 per invocation as a runaway guard; if reached with comments still open, report and ask the user before continuing |

Why the local loop is capped at two: local cubic iterations have sharply
diminishing returns and the same bot reviews the PR again anyway. Two passes
catch the mechanical issues; whatever survives them is either a false
positive or a judgement call, and both belong in the PR description and
then in the PR-mode loop, where the bot's comments are on the record. A local loop that
exits at the cap is **not a failure** — report the remainder and move on.

Why the PR loop runs to zero: cubic's PR comments are what a human reviewer
sees. The PR is not ready for them until every cubic thread is either fixed
(push + resolve) or answered with a won't-fix reply **and then resolved** —
a reply alone leaves `isResolved: false`, which step A would re-read as an
open issue every round and the loop could never exit clean.

## Inputs

- **Mode** (optional): `local` (default when the branch has no open PR) for
  CLI review, or `pr` (default right after the PR was opened in this task) for
  PR-based review.
- **Max iterations**: `local` mode — **2, not configurable upward**. `pr`
  mode — defaults to 5 per invocation as a runaway guard; the goal is zero
  comments, so on reaching the cap with comments still open, report and ask
  the user whether to run another round.

## Instructions

### 0. Scope

PR mode pushes to the remote and resolves review threads, so run it only on a
PR you authored or were asked to work on. Explicit read-only requests do not
enter this loop: inspect and report without pushes or resolutions.

### 1. Determine review mode

Check whether to use local CLI or PR-based review:

- If the user explicitly asked for "pr" mode, or this is the post-PR step of
  a PR (the agent opened the PR in this same task, or the user
  handed over a PR and asked to address cubic) -> PR mode. PR mode **pushes
  to the remote** on every iteration, so never select it on a PR the user
  did not put in scope — the mere existence of an open PR on the current
  branch is not opt-in.
- Otherwise -> local CLI mode (default).

For **local mode**, verify the CLI is installed:

```bash
which cubic
```

If not found, stop and suggest the user install it:

```bash
curl -fsSL https://cubic.dev/install | bash
```

Then verify it is authenticated:

```bash
cubic auth list
```

If no credentials are listed (or a review later fails with an auth error),
stop and tell the user to run `cubic auth login` — it is an interactive
browser sign-in to cubic.dev that an agent cannot complete on their behalf.

**Recommended setup — bring your Claude Code subscription.** After
`cubic auth login`, connect cubic to Claude Code so reviews run on Claude
models (`opus`, `sonnet`, or `haiku`) billed through your existing Anthropic
subscription:

```bash
cubic auth connect claude-code
```

cubic runs Claude Code as a subprocess via the Agent Client Protocol — your
Claude Code installation authenticates with Anthropic and makes every model
call; cubic never sees, stores, or proxies the credentials. Like
`cubic auth login`, this is an interactive step for the user, not the agent.
Details:
<https://docs.cubic.dev/ide/cli-review#bring-your-coding-subscription>

For **PR mode**, verify GitHub CLI access:

```bash
gh auth status
```

If `gh` is missing or unauthenticated, stop and tell the user they need GitHub CLI access for PR mode.

Then identify the PR:

```bash
gh pr view --json number,headRefName -q '{number: .number, branch: .headRefName}'
```

### 2. Loop (local: at most 2 iterations · PR: until zero comments)

Repeat the following cycle, at most **Max iterations** times (local mode:
**2**, always; PR mode: 5 per invocation, goal zero comments). Count an
iteration as one review run — the initial review is iteration 1, and in
local mode the review after the first round of fixes is iteration 2 and the
last one.

#### A. Run review

**Local mode:**

```bash
git status --porcelain
# Uncommitted changes that are part of this work -> cubic review -j
# Work already committed on the branch (porcelain clean, or showing only
# unrelated/untracked files) -> cubic review -b <base-branch> -j
# Both committed and uncommitted relevant changes -> commit the relevant
# files first (signed off), then cubic review -b <base-branch> -j
```

`-b/--base` takes the base branch name — pass it explicitly (e.g.
`cubic review -b main -j` — the 2.x line's branch).

Unrelated untracked or generated files do **not** count as "uncommitted
changes" — reviewing only the working tree when the real work is committed
on the branch would skip it entirely.

The CLI normally blocks until the review completes and prints a final
`issues` array. If it instead reports the review as queued/running (no final
`issues` array), poll by re-running the same `cubic review … -j` command:

- Sleep 15 seconds between retries
- Retry up to 20 times (5 minutes total)
- If still pending after 5 minutes, stop the loop and report timeout

**PR mode:**

```bash
git status --porcelain
# If there are uncommitted changes relevant to the PR, commit them first
# (signed off, staging only the specific files) — a bare push cannot publish
# them and the review would only cover the stale remote state.
git push
```

Then wait for GitHub/PR review results to settle before fetching issues:

- Poll every 30 seconds
- Retry up to 30 times (15 minutes total)
- Only proceed once PR review output is final
- If still pending after 15 minutes, stop the loop and report timeout

After completion, use `gh api graphql` against `repository.pullRequest.reviewThreads`.

- Paginate with `reviewThreads(first: 100, after: $after)` until `pageInfo.hasNextPage` is false
- Request thread fields: `id`, `isResolved`, `isOutdated`, `path`, `line`, `originalLine`, `startLine`, `originalStartLine`, `diffSide`, `startDiffSide`, `resolvedBy { login avatarUrl }`
- Request comment fields: `comments(last: 100) { nodes { id databaseId path line originalLine startLine originalStartLine author { login } body url createdAt } }`

Prefer unresolved, non-outdated review threads, and only act on comments authored by cubic's bot/app. If author identity cannot be determined, do **not** treat the PR's other review comments as loop input — human feedback lacks cubic's issue/priority structure and is not this loop's to resolve. Instead, list the unattributed comments in the final report and exit the loop.

#### B. Check exit conditions

Stop the loop if ANY of these are true:

- Zero issues found -> **success, code is clean**
- Only P3 (low) issues remain and no P0/P1/P2 -> **success, good enough** —
  unless the user requested "fix everything", in which case keep looping
  until the P3s are fixed too
- The previous iteration fixed nothing (every issue was skipped as a false
  positive, or the review returned the same set as last time) -> **stop,
  no progress** — re-running an identical review cannot change the outcome;
  report the remaining issues and why each was skipped. **In PR mode this
  exit follows the same rule as the cap:** each remaining thread is either
  answered with a won't-fix reply and resolved (so the PR reaches zero), or
  the agent reports and asks the user — never a silent exit with a thread
  still open, which would hand the human reviewer exactly the comment the
  loop exists to clear.
- Max iterations reached -> **stop, report remaining issues**. In local mode
  this is the second review: fix what it found (step C, one last time),
  commit (step D), and **do not run a third review** — the remainder goes
  into the report for the PR description and the PR-mode loop. In PR
  mode, comments still open at the cap mean the PR is not ready: report and
  ask the user before another round.

#### C. Fix issues

For each issue, prioritized P0 first then P1, P2:

1. Read the file and surrounding context
2. Validate the issue is real -> skip false positives
3. Fix in the simplest way possible without refactoring unrelated code
4. Track what was fixed for the summary

When there are multiple independent issues, use sub-agents where available to verify and fix them in parallel.

- Give each sub-agent one issue or one disjoint file set
- Ask each sub-agent to independently validate whether the issue is real before fixing
- Keep fixes isolated so they do not overwrite each other
- Review and integrate each sub-agent result before moving to the next review iteration

Skip P3 issues unless the user explicitly requested "fix everything".

#### D. Commit and continue

Stage only the files the fixes touched (never `git add -A` — it would sweep
in unrelated working-tree changes), and sign off the commit (this repo's DCO
check requires it):

```bash
git add <files fixed this iteration>
git commit -s -m "fix: address cubic review feedback (iteration N)"
```

If nothing was fixed this iteration, there is nothing to stage — skip the
commit and exit via step B's no-progress condition instead of looping.

Go back to step A.

### 3. Report

Report the PR's GitHub state, blockers, and the next action. For read-only
requests, report that nothing was pushed or resolved.

In PR mode, first push any fix commits from the final iteration — the
mid-loop push only happens at the top of step A, so commits made just before
a max-iterations exit would otherwise leave the PR stale. Then resolve the
cubic threads the pushed fixes addressed, and the won't-fix threads once
their reply is posted (team convention: a fix is push + resolve, silently; a
won't-fix is a reply with the reason, then resolve — the reasoning stays on
record and the thread stops counting as open), using the thread `id` from
the query in step A:

```bash
gh api graphql -f query='mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{isResolved}}}' -F id=<thread-id>
```

After exiting the loop, present a summary. In local mode the summary — in
particular its **remaining issues** list — goes into the PR description,
so keep it precise: file, line,
priority, the issue headline, and why it was left (false positive / judgement
call / cap reached).

## Output Format

**Clean exit:**

```
## Cubic Loop Complete

Iterations:   2
Issues fixed: 5
Remaining:    0

Changes:
- Iteration 1: Fixed SQL injection (P0), added null checks (P1 x2)
- Iteration 2: Fixed error handling (P2), added input validation (P2)

All fixes committed locally (pushed only in PR mode). Ready to open/merge the PR.
```

**Local cap reached (the normal pre-PR exit when the diff is not trivially clean):**

```
## Cubic Loop — Local cap reached (2 iterations)

Iterations:   2 (cap)
Issues fixed: 6
Remaining:    2

Remaining issues (carried into the PR description and the PR-mode loop):
- src/helpers.ts:234 — P2: "Consider extracting this into a utility function" — judgement call, left as is
- src/config.ts:12 — P3: "Magic number could be a named constant" — P3, not fixed by default

Next: open the PR.
```

**PR-mode cap or no-progress exit with threads still open** (the exit itself
does not end the job — the threads do; the header names which exit fired and
the iteration count matches it):

```
## Cubic Loop — PR mode, stopped after 5 iterations with comments open

Iterations:   5 (cap)
Issues fixed: 11
Remaining:    2 unresolved cubic threads

Remaining threads:
- src/helpers.ts:234 — P2: "…" — <why it is still open>

The PR is not ready for human review until these are resolved or answered.
Continue for another round?
```

```
## Cubic Loop — PR mode, no progress after iteration 3 with comments open

Iterations:   3 (iteration 3 fixed nothing)
Issues fixed: 4
Remaining:    1 unresolved cubic thread

Remaining threads:
- src/helpers.ts:234 — P2: "…" — judged a false positive: <reason>

Reply won't-fix + resolve this thread (reaching zero), or say so and I stop here.
```
