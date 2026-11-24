**Build-log follower HTTP errors** — `follow_build_logs()` / `followBuildLogs()`
(images and sandboxes) now read a failed discovery or stream response before
deciding whether to retry it. When the reconnect budget runs out, the
`BuildLogInterruptedError` carries the last response's typed error (status,
code, message and trace ID) as its cause instead of only "HTTP 503". A 501
(not implemented), or a 429 / 503 whose code names a state a retry cannot
change (`LIMIT_EXCEEDED`, `V0_BACKUP_REGISTRY_UNCONFIGURED`), is no longer
retried: from discovery it is raised as its typed error, as a 404 is. Other
429 and 5xx responses, `SERVICE_UNAVAILABLE` included, are still retried,
honouring `Retry-After`, including one whose error body is too large to read.
Go's `FollowBuildLogs` follows the same rules.
