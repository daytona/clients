**CLI: management lists return one page** — `org`, `project`, `member`,
`invite`, `key`, `org-key`, `service-account`, `project member` and `project
service-account` list return one page with `--limit` and `--cursor`, like the
other lists; `--all` reads every page (they always read every page).
