**Build-log followers keep the server's error** — when
`follow_build_logs()` / `followBuildLogs()` / `FollowBuildLogs` (images and
sandboxes) run out of reconnect attempts, the `BuildLogInterruptedError` now
carries the last failed response's typed error (status, code, message and
trace ID) as its cause, instead of only "HTTP 503". The response is read
before the retry decision; an error body that is too large to read keeps its
status. In Python and TypeScript, an error body that does not finish within
the request timeout (30 seconds when the client has none) keeps its status
too, so a follower without a timeout never waits on it forever.
