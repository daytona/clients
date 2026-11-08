**CLI: a `--timeout` with nothing to wait for is refused** — with `--no-wait`,
on `image logs` and `sandbox process logs` without `--follow`, and on
`sandbox docker status` without `--wait`, `--timeout` is a usage error
instead of being ignored.
