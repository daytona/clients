**CLI flags only where they apply** — `--file/-f`, `--no-wait` and `--timeout`
exist only on the commands that use them; elsewhere they are unknown flags
(exit 2) instead of being ignored. `--limit` is checked to be 1–200 before any
request. Organization, project, member, invitation, key, org-key and
service-account lists return one page with `--limit`, `--cursor` and `--all`
like the other lists. Repeated `--role` / `--permission` flags accumulate.
