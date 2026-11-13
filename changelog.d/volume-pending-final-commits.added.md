**Volume pending final commits** — a volume reports the ended sandboxes whose
last writes to it are not saved yet: `Volume.pending_final_commits` (Python,
a tuple of `PendingFinalCommit`) / `Volume.pendingFinalCommits` (TypeScript),
each entry with the sandbox id, a `status` (`pending`, `saving`, `failing`, or
`unknown` with the original in `raw_status` / `rawStatus`), `ending_at` /
`endingAt` and `last_failure_at` / `lastFailureAt`. The list is empty when
nothing is pending, and when the API does not report it. The CLI shows it in
`volume get` and `volume list` JSON, and in the `volume get` table while there
are entries.
