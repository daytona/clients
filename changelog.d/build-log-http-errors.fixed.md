**Build-log follower HTTP errors** — `follow_build_logs()` / `followBuildLogs()`
(images and sandboxes) now read a failed discovery or stream response before
deciding whether to retry it. When the reconnect budget runs out, the
`BuildLogInterruptedError` carries the last response's typed error (status,
code, message and trace ID) as its cause instead of only "HTTP 503". A 429 or
5xx whose code names a state a retry cannot change (for example
`LIMIT_EXCEEDED`) is no longer retried: from discovery it is raised as its
typed error, as a 404 is. Other 429 and 5xx responses, `SERVICE_UNAVAILABLE`
included, are still retried, honouring `Retry-After`. Go's `FollowBuildLogs`
stops retrying the same codes.
