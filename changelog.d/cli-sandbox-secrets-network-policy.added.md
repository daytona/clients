**CLI: manage a running sandbox's secrets and network policy** —
`sandbox secrets get|set` reads and replaces the project secrets attached to
a sandbox (`--secret NAME…` or `--clear`), and `sandbox network-policy
get|set|reset` reads, replaces (the `sandbox create` flags) and removes its
egress policy. Updates send `If-Match`: with `--if-match` (the `etag` that
`get` prints) a change made since that read is refused with 412 instead of
overwritten; without it the command reads the current etag just before
writing, so it replaces whatever is current.
