**CLI: lifecycle actions follow the sandbox** — `--on-lifecycle-timeout` and
`--on-idle-timeout` accept `delete` on any sandbox and `stop` only on a
persistent macOS sandbox; `pause`, or `stop` elsewhere, is a usage error
before any change.
