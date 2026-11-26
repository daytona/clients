**Long `sandbox.volumes.pull()` waits in rounds** — the pull timeout
(Python `timeout=`, TypeScript `timeoutSeconds`, Go `PullVolumeOptions.Timeout`)
is no longer capped at 30 seconds. A wait up to 30 seconds is still one
request; a longer one is spent in rounds of at most 30 seconds (each read
bounded at 35 seconds) with the same request ID while the result is
`pending`, so no request stays open for the whole wait. A value above about
24.8 days (Go: also `daytona.WaitForever`) has no deadline. Errors are still
never retried.
