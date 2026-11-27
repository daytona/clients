**CLI: `image list --name ''` sends the empty name, as the SDKs do** — an
explicit empty name is sent as `name=` instead of being dropped, so the CLI
and the SDKs make the same request. An empty `--sandbox` on `image list` and
`snapshot list` is a usage error (exit 2).
