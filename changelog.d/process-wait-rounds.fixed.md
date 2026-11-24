**Bounded process waits** — `process.run()`, `session.run()` and
`ProcessHandle.wait()` (Python, TypeScript; Go `Exec`, `Run`, `Session.Run`,
`ProcessHandle.Wait`) no longer hold one request open for the whole wait
budget, or indefinitely without one: every request holds for at most 30
seconds, and the wait continues in further rounds until the caller's
deadline. A network path that drops long, quiet connections now costs one
round, which the wait retries, instead of failing or stalling the call. The
overall `timeout` / `timeoutSeconds`, the execution timeout and the results
are unchanged; the request that starts the process is never retried.
