**CLI: `sandbox create --wait` is now `--server-wait`** — the flag that asks the
server to hold the create response no longer reads like the CLI's own wait;
`--wait` is refused with a hint naming `--server-wait`, and `--server-wait`
with `--no-wait` is a usage error.
