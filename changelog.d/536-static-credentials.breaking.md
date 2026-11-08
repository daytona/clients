**CLI: static credentials are sent as-is** — `--api-key`, `DAYTONA_API_KEY`
and a `login --token` credential are used as given on every request; the CLI
no longer mints or caches a project key from them when a sandbox route
refuses them. For sandbox commands, pass a project API key with the
permissions they need, or sign in with `daytona login`.
