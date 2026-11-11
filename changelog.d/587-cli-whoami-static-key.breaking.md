**CLI: `whoami` checks a static credential** — with `--api-key`,
`DAYTONA_API_KEY` or a `login --token` profile, `whoami` asks the API about the
key: a refused key exits 3, an unreachable API or a server error fails the
command (it exited 0 without asking), and a project API key reports its organization and
project (`org_id`, `project_id`) and, in `api_key`, the API's record of the key
(its name, key prefix and suffix, expiry, permissions and the organization and
project it acts in).
