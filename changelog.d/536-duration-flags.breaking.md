**CLI: durations are Go durations** — the duration flags take a value such as
`90s`, `30m` or `1h`. Those the API stores in seconds take whole seconds:
`--wait`, `--create-ttl` (`max` for the server maximum, was `-1`),
`--idle-timeout` (at least `1m`; `off` on `sandbox update`, was `-1`),
`--lifecycle-timeout` and `sandbox port preview-url --ttl`. `--timeout`, the
CLI's own wait, takes any positive duration. `--file` bodies keep the API's
integer `*_seconds`.
