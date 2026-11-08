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
`request_timeout`. In both SDKs a positive duration below a millisecond is
sent as 1 ms (never `0`, which means no limit), a caller budget or process
deadline above 2,147,483 seconds (the longest timer) means no limit, as the
request timeout does, and a git clone / pull / push `timeout` bounds the whole
request (Python used it as a per-phase HTTP timeout).
