**CLI: a missing login is reported first** — with no credential at all,
commands that need an organization or a project (`key list`, `invite list`,
`project use`, `audit list`, …) report "not logged in" (exit 3) before
asking for `--org` / `--project` (exit 2).
