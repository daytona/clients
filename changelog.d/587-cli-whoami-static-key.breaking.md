**CLI: `whoami` checks a static credential** — with `--api-key`,
`DAYTONA_API_KEY` or a `login --token` profile, `whoami` asks the API about the
key: a refused key exits 3, and a project API key reports its organization and
project (`org_id`, `project_id`) and the key's description in `api_key`.
