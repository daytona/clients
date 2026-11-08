**CLI: durations are Go durations** — every duration flag takes a value such
as `90s`, `30m` or `1h` in whole seconds: `--wait`, `--create-ttl` (`max` for
the server maximum, was `-1`), `--idle-timeout` (at least `1m`; `off` on
`sandbox update`, was `-1`), `sandbox port preview-url --ttl`. `--file`
bodies keep the API's integer `*_seconds`.
