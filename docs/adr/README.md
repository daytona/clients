# Architecture decision records

Decisions about the SDK and CLI surface that outlive a single pull request:
what was decided, why, and what was rejected. Each record is one file,
`NNNN-<short-title>.md`, numbered in the order it was written.

## Format

```markdown
# NNNN. Title

Status: proposed | accepted | implemented | superseded by NNNN

## Context
What problem the decision answers, in terms of the SDK and CLI surface.

## Decision
What was decided.

## Alternatives
What else was considered, and why it was rejected.

## Consequences
What follows for users and for contributors, including what is now hard to
change.
```

## Rules

- Write for a reader who knows the SDKs but not the history.
- Cover SDK and CLI behavior only. Describe the API through the contract under
  [`contract/`](../../contract/), not through how the platform implements it.
- A PR that changes a recorded decision updates the record (or supersedes it
  with a new one) in the same PR.
- Records are not changelogs: user-visible changes still get a
  [`changelog.d/`](../../changelog.d/) fragment.
