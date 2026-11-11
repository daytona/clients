**`DAYTONA_SANDBOX_TOKEN` is a reserved environment name on create** — a
sandbox create whose `env` sets `DAYTONA_SANDBOX_TOKEN` now fails with
`BadRequestError` (400) naming the key, like every other reserved `DAYTONA_*`
name; `DAYTONA_API_URL`, `DAYTONA_OTEL_ENDPOINT` and `DAYTONA_DOCKER_START`
stay settable. The READMEs and the CLI README list the rule.
