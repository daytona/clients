**CLI: `sandbox exec --tag` and `sandbox process list --tag`** — `exec --tag`
starts a tagged process and `process list --tag` lists the tagged processes
(filtered by the CLI too, for a sandbox that ignores the filter). A tag that
is not 1-128 characters of `[A-Za-z0-9._-]` (or is `.` / `..`) is a usage
error before any request.
