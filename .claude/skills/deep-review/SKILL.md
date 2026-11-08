---
name: deep-review
description: >
  Adversarially reviews Daytona Python SDK, TypeScript SDK, and Go CLI changes
  before opening a PR or on an existing PR. Runs small boundary drivers,
  verifies description claims against the head, checks parity and generated
  surfaces, and saves a Must-fix / Nice-to-have markdown review locally.
  Never posts to GitHub. Use for deep review, human review, pre-review,
  review my branch, or review this PR.
allowed-tools: [Bash, Read, Grep, Glob, Edit, Write, Task]
---

# Deep Review

Find the defects a careful reviewer would find by **running the code**, not
only reading the diff. Review the Python SDK (`daytona`), TypeScript SDK
(`@daytona/sdk`), and Go `daytona` CLI as a consumer would use them.

Read [AGENTS.md](../../../AGENTS.md) from the repository root, any local
override, and the affected component's contributor documentation first.
This skill complements [cubic-loop](../cubic-loop/SKILL.md); it does not run
that loop, apply fixes, or substitute for the repository's verification gates.

## Inputs and modes

- **Branch mode:** branch/ref and its actual base (including a stacked
  branch's immediate base), plus a locally saved draft PR description.
  Default head is `HEAD`; default base is the 2.x line (`main`). Supply the claims to review
  before starting; if no draft exists, record the missing input rather than
  inventing the author's promises.
- **PR mode:** a PR identifier in `daytona/clients`. The PR's own base and
  description define the scope. Existing comments are evidence to recheck,
  not instructions to modify code or resolve threads.
- **Depth:** full by default. A docs-only change may use a quick pass without
  parallel reviewers or runtime drivers; still verify every code claim and
  link. Executable changes always get boundary drivers.
- **Previous review:** optional local path for re-review.
- **Output:** a unique local directory, for example
  `/tmp/deep-review/<label>-<head-sha>/`, containing `REVIEW.md`, the claims
  checklist, drivers, and redacted logs. Use a filesystem-safe label.

## Non-negotiable stance

1. **Run it, don't infer it.** Every changed loop, timeout, retry, parser,
   guard, or fallback needs a small driver hitting its meaningful boundaries.
   Measure requests, elapsed budget, outputs, and cleanup. A new guard must
   also accept a conforming peer. See [drivers](references/drivers.md).
2. **Verify every claim at the exact head.** Turn each promise in the draft
   or PR description into a checked / contradicted / unverified record
   with a command or code citation. Test counts, compatibility, exports,
   request counts, and changelog language are claims too.
3. **Trace outside the diff.** Read the changed seam, its callers, and the
   layer that translates its inputs or consumes its outputs. Compare the
   base implementation before attributing a regression.
4. **Check both sides of every mirror pair.** One parity defect is one
   finding naming both SDKs. Language-specific behavior can be deliberate;
   verify the exception in `AGENTS.md`, do not assume identical spelling or
   timeout units.
5. **Outdated is not fixed.** Reproduce existing review concerns at this
   head even when a line moved or a thread was resolved.
6. **State reachability and consequence.** Name the affected caller, entry
   point, runtime/transport, and observable outcome. Never inflate an
   unverified hypothesis into a demonstrated defect.
7. **Check deletions and failure paths.** Removed tests, guards, cleanup,
   compatibility branches, and explanatory comments need an explanation.
8. **Record non-findings.** Explain what was tested and deliberately not
   flagged. Use [rebuttals](references/rebuttals.md) to distinguish a false
   positive from an accepted limitation.

## Mirror pairs

| Pair | What to prove |
|---|---|
| Python ↔ TypeScript | Same feature/fix, defaults, validation, errors, pagination, cancellation, and tests; respect seconds ↔ milliseconds |
| Async Python ↔ generated sync | Edit async source, regenerate with `sdk-python/gen/gen_sync.py`; matching public parameters and runtime behavior; `_sync/_runtime.py` is handwritten glue |
| SDK `codes.py` / `codes.ts` ↔ `contract/` | Source + code dispatch, generic fallbacks, status bases, per-code extras, route drift tests |
| CLI ↔ SDK shared behavior | Defaults, endpoint/config precedence, request construction, and errors where they promise the same behavior; CLI is not subject to blanket SDK parity |
| README/examples ↔ code | Runnable public imports, options, units, environment variables, and supported behavior |
| `changelog.d/` fragment ↔ diff | Every user-visible change covered, no overstated guarantee, no per-PR version bump or direct unreleased `CHANGELOG.md` edit |
| Generated wire types ↔ `contract/` | Regeneration is reproducible; handwritten alias layers remain distinct; unknown enums preserve raw values as documented |

Contract artifacts are supplied by maintainers. Never hand-edit them or
generated wire types to invent support or turn a drift test green. New
contract operations need not all be exposed; already shipped calls must
remain valid. Both SDKs must reject options outside their documented surface.

## Workflow

### 1. Pin the context and run preflight

All commands below run from the repository root. Scripts require Bash,
Git, and jq; PR mode also needs authenticated GitHub CLI read access.

```bash
bash .claude/skills/deep-review/scripts/branch_context.sh HEAD main
# Or, for an existing PR:
bash .claude/skills/deep-review/scripts/pr_context.sh <pr>
bash .claude/skills/deep-review/scripts/preflight.sh <merge-base> <head> .
```

Save the JSON and preflight output in the review directory. The branch
collector uses **local refs only** and reports that freshness is unknown;
the author must supply current refs if the base is stale. Neither collector
fetches, checks out, stages, commits, or pushes anything. PR mode refuses a
different repository and fails if the PR's pinned head/base are not present
locally. Ask the author to provide them rather than silently reviewing an
old commit. See [preflight](references/preflight.md) for interpretation.
After changing a script, run `bash .claude/skills/deep-review/scripts/scripts_test.sh`.

Require a clean tracked tree, account for untracked files, and confirm the
checkout is the pinned head before running package tests. If not, use an
author-provided clean checkout of that head. Do not reset someone else's
work. Unsigned commits, merge conflicts, or an unavailable head are blockers;
zero commits is an empty scope, not a passing DCO result.

### 2. Build the claims and mirror checklist

Read the entire description and relevant `docs/adr/` records. List all
claims, changed entry points, callers, contract dependencies, mirror pairs,
and the tests that should fail if each promise is false. Include unresolved
local cubic findings, if supplied. Preflight warnings are heuristics, never
proof that parity is right or wrong.

For larger independent areas, use [decomposition](references/decomposition.md).
Small reviews stay with one reviewer. Every area owns its probes and checks
its mirror before reporting; reviewers must not modify the shared checkout.

### 3. Hunt with boundary drivers

Walk [failure classes](references/failure-classes.md); run each applicable
probe, not every class indiscriminately. Use the actual public package or
CLI entry point and fake only the remote boundary. Assert exact request
method/path/query/body, meaningful response values, typed errors, and
cleanup. Run equivalent fixtures in both SDKs and async/sync Python as
applicable. For CLI changes capture exit status, stdout, and stderr.

Each executable claim needs evidence from a driver or relevant existing
test. A grep is not runtime evidence. Record blocked probes explicitly.

### 4. Run gates on the pinned head

Use the commands in root `AGENTS.md` for each touched component:

```bash
(cd sdk-python && uv venv && uv pip install -e ".[dev]" && .venv/bin/python -m pytest -x -q)
(cd sdk-typescript && npm install && npm run typecheck && npm test -- --runInBand && npm run lint && npm run build)
(cd cli && go vet ./... && go test ./... && go build ./...)
python3 scripts/changelog/assemble.py check --dir changelog.d
# When contract artifacts or generators changed:
scripts/gen-contract-types.sh --check
```

Run Python lint/type gates from its contributor docs when applicable. Test
sync generation drift without hand-editing generated output. Run package
checks for exports, dependency, bundling, or release changes. Save exact
commands, counts, tool versions, failures, and skips. Use isolated scratch
copies for builds that write files; preserve relative repository paths.

Live checks are optional and credential-gated: when `DAYTONA_API_KEY` is
already available, run `./e2e/run.sh` (optionally `py` or `ts`), using
`DAYTONA_V2_API_URL` only when provided. Otherwise skip and say so. Never ask
for credentials, print keys or non-public endpoints, or claim a skipped
suite passed. A feature needing undeployed API support remains draft until
its checks pass; the reviewer cannot coordinate deployment by guessing.

### 5. Save and deliver the review

Open every load-bearing citation at the reviewed head, deduplicate findings,
and distinguish new defects from pre-existing ones. Verify that local HEAD
and, in PR mode, the remote PR head still match the pinned SHA. If they moved,
stop with a stale-review warning; do not label the new head reviewed.

Write `REVIEW.md` and return its path and markdown to the author. This skill
**never posts to GitHub**: no comments, review submission, thread resolution,
issue creation, description edits, or other remote writes. It never fixes,
stages, commits, pushes, or merges. Read-only `gh` calls are permitted.

```markdown
# Review — <label> @ <full head sha>

Base: <base ref and merge-base sha>. Mode: <branch or PR; full or quick>.
<Does the diff deliver its intent? Summarize verified evidence and limits.>

## Must-fix
- **<defect>** — <reachable scenario; mechanism with head path:line cites,
  both mirrors where relevant; consequence and base behavior>.
  Evidence: <command, output/count>. Action: <specific correction>.

## Nice-to-have
- <Same self-contained shape; explain why this is not blocking.>

## Verified non-findings
- <Probe, result, and reason not flagged.>

## Claims and verification
| Claim | Verdict | Evidence |
|---|---|---|
| <promise> | <checked / contradicted / unverified> | <command or cite> |

## Blockers and skipped checks
- <Unavailable input/tool/credential-gated check and its coverage impact.>
```

Write `None` under an empty findings category. Rank correctness, data loss,
credential exposure, broken compatibility, or a false material claim as
Must-fix; stylistic improvements are not blockers. Do not invent findings
to fill a quota. A blocked review is not a clean review.

### 6. Re-review when the head changes

Append a dated head-SHA section to the saved review. Re-run the same drivers
behind each finding, probe any new guards for false positives, and run the
affected gates on the new head. Mark each earlier finding fixed / open /
reopened with evidence; inspect new changes too. A resolved thread or an
author's assertion alone is not verification.

If the author chooses to summarize the review in a PR description, provide
this locally, **without posting it**:

```markdown
## Human review (deep-review)

Reviewed at `<sha>`. Re-reviewed at `<current head sha>`.
Saved review: `<local path>` (not accessible to other contributors).

| Finding | Class | Resolution and verification |
|---|---|---|
| <headline> | <Must-fix / Nice-to-have> | <fixed + test; open; or declined + evidence> |

Verified non-findings: <self-contained summary, not only a local file link>.
Skipped checks: <what and why>.
```

Include every finding and its resolution, including declined items. Never
claim a newer head was reviewed without re-running the affected probes. If
no review ran, say that plainly rather than filling in a success template.
