**CLI: malformed credentials and missing logins are reported as such** — a
credential with a control character (such as a trailing line break) or a
non-ASCII character, from `--api-key`, `DAYTONA_API_KEY`, a saved profile or
`daytona login --token`, is refused before any request (exit 2) with an error
naming its source and never its value; before, a control character was
reported as a network problem ("check the API URL … and your network") and a
non-ASCII one was sent as raw UTF-8. With no credential at all, commands that
need an organization or a project (`key list`, `invite list`, `project use`,
`audit list`, …) now report "not logged in" (exit 3) before asking for
`--org` / `--project` (exit 2).
