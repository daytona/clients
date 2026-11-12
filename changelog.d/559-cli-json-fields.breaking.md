**CLI: removed API fields leave the JSON output** — `--output json` / `yaml`
prints the API's response as it is, so once the API stops sending them these
keys disappear: `v1_membership_sync_enabled` from `daytona org` output (use
`v0_membership_sync_enabled`), `sync_to_v1` from `daytona key` output (use
`sync_to_v0`), and `sandboxes_per_mac` from `daytona reservation` output (a
model's limit is `max_sandboxes` in `daytona mac-model list`).
