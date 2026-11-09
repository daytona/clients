**CLI: an unknown profile is an error** — a `--profile` or `DAYTONA_PROFILE`
that names no saved profile exits 5 on the commands that use the selected
profile, instead of an empty profile being made up; `profile`, `config`,
`version`, `help`, `docs` and completion still run. Only
`login --profile NAME` creates a profile.
