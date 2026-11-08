**Process log stream drops** — TypeScript `streamLogs()` throws
`ConnectionError` (source `SDK`, the runtime's error as its `cause`) when the
log stream is cut short, as Python raises it, instead of the runtime's own
error; both SDKs document that an iteration ending without an `eof` event did
not reach the end of the log and resumes from the last event's cursor.
