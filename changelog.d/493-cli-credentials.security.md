**CLI credential handling** — a cached platform token is sent only to the
API that issued it; with `--api-url` (or `DAYTONA_V2_API_URL` /
`DAYTONA_API_URL`) pointing at another API, `daytona` exchanges a new token
there when that API issues them. `daytona profile delete` now revokes the
deleted profile's cached login-minted project keys, as `daytona logout` does.
`daytona login --token ""` (for example, an unset CI secret) fails with a usage
error instead of starting an interactive login.
