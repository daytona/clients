**Bounded process waits** — `process.run()`, `session.run()` and
`ProcessHandle.wait()` (Python, TypeScript; Go `Exec`, `Run`, `RunArgs` and
`Session.Run` without `Stdout` / `Stderr` writers, and `ProcessHandle.Wait`)
no longer hold one request open for the whole wait budget, or indefinitely
without one. The sandbox holds each request, including the process-record
read after an expired round, for at most 30 seconds, and the SDK bounds its
read at 35 seconds; the wait continues in further rounds until the caller's
deadline. A network path that drops long, quiet connections now costs one
round, which the wait retries, instead of failing or stalling the call. When
the round that spends the budget ends, the process record is read once more,
so a process that ended with it is reported instead of `timed_out` (a
timed-out wait may therefore return a few seconds past the budget). The
overall `timeout` / `timeoutSeconds`, the execution timeout and the results
are otherwise unchanged; the request that starts the process is never
retried. A Go run with `Stdout` or `Stderr` set still follows its output on
one log stream for the whole run, bounded only by its `Timeout` and the
context.
