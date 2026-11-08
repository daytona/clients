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
`request_timeout`. In both SDKs a positive duration the SDK sends to the
sandbox in milliseconds (a wait, an execution timeout, an escalation delay, a
key-press delay) is sent as at least 1 ms when it is below a millisecond
(never `0`, which means no limit); a caller budget or process deadline above
2,147,483.647 seconds (the longest JavaScript timer) means no limit, as the
request timeout does, also for values too large for a float, which no longer
raise `OverflowError` in Python; and a git clone / pull / push `timeout`
bounds the whole request (Python used it as a per-phase HTTP timeout).
