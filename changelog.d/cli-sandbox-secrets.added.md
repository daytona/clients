**CLI: `sandbox secrets get|set`** — read and replace the project secrets
attached to a running sandbox (`--secret NAME…` or `--clear`); values are
never sent or printed. `set` sends `If-Match`: with `--if-match` (the `etag`
that `get` prints) a change made since that read is refused with 412 instead
of overwritten; without it the command reads the current etag just before
writing, so it replaces whatever is current.
