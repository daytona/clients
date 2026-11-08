**CLI credentials and environment** — `--api-key`, `DAYTONA_API_KEY` and a
`login --token` credential are sent as-is: the CLI no longer mints or caches a
project key from them when a sandbox route refuses them. An empty `--api-key`
is a usage error instead of falling back to `DAYTONA_API_KEY` or the saved
login. A `--profile` / `DAYTONA_PROFILE` that does not exist exits 5 (except
`login --profile`). `DAYTONA_API_URL` (0.x) and `DAYTONA_REGION` are no longer
read: use `DAYTONA_V2_API_URL` / `--api-url` and `--region`. `whoami` puts only
resolved IDs in `org_id` / `project_id` and reports `context_resolved`.
