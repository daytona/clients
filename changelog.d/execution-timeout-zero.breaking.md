**An execution timeout of `0` means no deadline** — `execution_timeout=0` /
`executionTimeoutSeconds: 0` on `process.start()` / `run()`, `sandbox.exec()`,
`session.start()` / `run()` and `pty.start()` no longer sends `timeoutMs: 0`,
which made the sandbox kill the process at once; the SDKs omit the deadline, as
they do for a value above the longest timer, so the process runs without one,
as every other `0` duration in the SDKs means.
