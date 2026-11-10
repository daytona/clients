**CLI: a saved credential stays with its API URL** — a login or `login --token`
credential is no longer sent, refreshed or exchanged at another API named by
`--api-url` or `DAYTONA_V2_API_URL`: such a command is a usage error (exit 2).
Pass `--api-key` / `DAYTONA_API_KEY` for the other API, or sign in to it with
`daytona login --api-url URL --profile NAME`. An API URL that is not an
absolute `http://` or `https://` URL is a usage error too (it was exit 1).
