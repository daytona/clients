**One process log stream in both SDKs** — `stream_logs()` / `streamLogs()`
yield a tagged `StreamLogEvent`: `log` (a frame), `state` (the process
ended, with its `WaitResult`), `warning` (older frames were evicted) and
`eof`, with named member types (`StreamLogFrameEvent`,
`StreamLogStateEvent`, `StreamLogWarningEvent`, `StreamLogEofEvent`). A
stream error raises the typed error for its code instead of being yielded
(TypeScript) as an untyped event. Python yielded bare `LogFrame`s and gains
`limit=`.
