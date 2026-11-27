**CLI: manage a running sandbox's secrets and network policy** —
`sandbox secrets get|set` reads and replaces the project secrets attached to
a sandbox (`--secret NAME…` or `--clear`), and `sandbox network-policy
get|set|reset` reads, replaces (the `sandbox create` flags) and removes its
egress policy. Updates are conditional: they send `--if-match` (the `etag`
that `get` prints) or else the etag of a read made just before, so a change
made in between is refused with 412 instead of overwritten.
