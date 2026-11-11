**Safe branch deletion** — `git.delete_branch()` / `git.deleteBranch()` now
deletes only a merged branch: an unmerged one raises `ConflictError`
(`CONFLICT`). Pass `force=True` (Python) / `{ force: true }` (TypeScript) to
delete it anyway; the option is sent only when true. The checked-out branch is
never deleted.
