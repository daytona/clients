**CLI: `--file` `wait_seconds` with `--no-wait` is a usage error** — on
`sandbox create*`, a `--file` body with `wait_seconds` together with `--no-wait`
exits 2, like `--server-wait` with `--no-wait`.
