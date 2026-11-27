**CLI: `image list --name ''` sends the empty name, as the Python and
TypeScript SDKs do** — an explicit empty name is sent as `name=` instead of
being dropped. An empty `--sandbox` on `image list` and `snapshot list` is a
usage error (exit 2).
