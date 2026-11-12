**Build-log follow raises the first request's HTTP error unchanged** —
`images.follow_build_logs()` / `followBuildLogs()` and the sandbox's
`follow_build_logs()` / `followBuildLogs()` raise an HTTP error of their initial
discovery request (a 404 for an unknown image or a sandbox without an image
build, a 401, …) as its typed error (`NotFoundError`, …), as `build_logs()` /
`buildLogs()` already did, instead of wrapping it as
`BuildLogInterruptedError`. Once the follower is running, failures are still
`BuildLogInterruptedError` with the last cursor.
