**CLI durations** — `--timeout DURATION` is only the CLI's own wait. The
sandbox lifecycle pair is `--lifecycle-timeout DURATION` /
`--on-lifecycle-timeout ACTION` on `sandbox create`, `create-from-snapshot`,
`create-macos` and `update` (was `--timeout SECONDS` / `--on-timeout`). Every
duration flag takes a Go duration in whole seconds: `--wait 30s`,
`--create-ttl 1h` (`max` for the server maximum, was `-1`), `--idle-timeout
30m` (`off` on `update`, was `-1`), `sandbox port preview-url --ttl 5m`.
`--file` bodies keep the API's integer `*_seconds`.
