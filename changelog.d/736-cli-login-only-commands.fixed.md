**CLI refuses API keys on login-only commands** — the commands whose routes
admit only a `daytona login` session (`billing`, `invite`, `key`, `member`,
`org-key`, `project`, `service-account`, and `org` except `org limits`) now
refuse a static credential (`--api-key`, `DAYTONA_API_KEY`, or one saved with
`login --token`) before choosing an organization or sending a request, with
exit 3 and a message saying that a login session is needed, instead of asking
for `--org` and then reporting the same "credential rejected" 401 as an
invalid key. A bearer shaped like a JWT is still sent; when the server refuses
it (or an expired login) on these commands, the hint says the command needs a
`daytona login` session. Their help says so.
