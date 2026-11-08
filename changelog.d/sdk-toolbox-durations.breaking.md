**Toolbox durations in seconds** — TypeScript process, session, PTY,
interpreter and git options take seconds and say so: `timeout` becomes
`timeoutSeconds` (`run()`, `wait()`, `session.run()`, `interpreter.run()`,
`context.run()`, `git.clone()` / `pull()` / `push()`), `executionTimeout`
becomes `executionTimeoutSeconds` and `kill({ escalateAfter })` becomes
`escalateAfterSeconds`; `ProcessInfo.executionTimeoutSeconds` reports the
deadline in seconds. Python keeps `timeout`, `execution_timeout` and
`escalate_after` (already seconds). Unix-millisecond timestamps carry the
unit: `LogFrame.timestamp` and `FilesystemEvent.timestamp` become
`timestampMs` / `timestamp_ms`. Python `create_context()` no longer takes
`request_timeout`.
