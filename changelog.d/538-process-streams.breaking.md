**One process log stream in both SDKs** — `stream_logs()` / `streamLogs()`
yield a tagged `StreamLogEvent`: `log` (a frame), `state` (the process ended,
with its `WaitResult`), `warning` (older frames were evicted) and `eof`, with
named member types (`StreamLogFrameEvent`, `StreamLogStateEvent`,
`StreamLogWarningEvent`, `StreamLogEofEvent`). A stream error raises the typed
error for its code instead of being yielded (TypeScript) as an untyped event.
Every event carries its resume `cursor` (the payload's, else, only when the
payload has none, the SSE `id:` line's, in both SDKs; a payload cursor that is
not a non-empty string is refused) and a warning its `first_available_cursor`
/ `firstAvailableCursor`; an event missing one, or a warning or error without
its message, raises `BadRequestError` (400, `BAD_REQUEST`, source `SDK`). In
both SDKs an SSE block without a `data:` line, or an unterminated final block,
dispatches nothing (TypeScript dispatched both). Python yielded bare
`LogFrame`s and gains `limit=`.
