**CLI: `sandbox network-policy get|set|reset`** — read, replace (with the
`sandbox create` flags `--allow-domain`, `--allow-cidr`, `--block-all`) and
remove a running sandbox's egress policy. `set` and `reset` send `If-Match`:
with `--if-match` (the `etag` that `get` prints) a change made since that
read is refused with 412 instead of overwritten; without it the command reads
the current etag just before writing, so it replaces whatever is current.
