**Process log stream ends** — a followed process log that is cut short, or
that ends without its `eof` event, raises `ConnectionError` (source `SDK`) in
both SDKs: the sandbox ends the stream with `eof`, so an iteration that ends
normally always has the whole log. TypeScript threw the runtime's own error
for a cut-short stream and both SDKs ended silently without `eof`; resume with
`stream_logs(cursor=...)` / `streamLogs({ cursor })` from the last event's
cursor.
