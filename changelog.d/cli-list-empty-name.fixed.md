**CLI: `image list --name ''` matches nothing, as in the SDKs** — an explicit
empty name is sent as `name=` instead of being dropped, so it no longer lists
every image. An empty `--sandbox` is a usage error (exit 2).
