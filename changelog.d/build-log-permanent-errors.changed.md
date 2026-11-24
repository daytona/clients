**Build-log followers stop retrying permanent errors** — a 501 (not
implemented), or a 429 / 503 whose code names a state a retry cannot change
(`LIMIT_EXCEEDED`, `V0_BACKUP_REGISTRY_UNCONFIGURED`), is no longer retried by
`follow_build_logs()` / `followBuildLogs()` / `FollowBuildLogs`: from discovery
it is raised as its typed error, as a 404 is, and on the log stream it ends
the follow at once. Other 429 and 5xx responses, `SERVICE_UNAVAILABLE`
included, are still retried, honouring `Retry-After`.
