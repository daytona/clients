**Three more API error codes** — both SDKs' `codes` catalog knows them, each
mapped to its error class:

- `SANDBOX_ABUSE_HOLD` (403, `ForbiddenError`): a sandbox stopped after
  suspected abuse refuses start, resume, fork, snapshot and image capture
  until an operator releases it.
- `GPU_ACCESS_UNDER_REVIEW` (403, `ForbiddenError`): a GPU sandbox create is
  refused while the organization's GPU access is under review.
- `MEMBERSHIP_EDITS_FROZEN` (409, `ConflictError`): membership changes in the
  organization (invitations, member updates and removals) are paused.
