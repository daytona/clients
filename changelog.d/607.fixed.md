**Interpreter runs with very large output** — `interpreter.run()` and
`process.run()` no longer fail with `CursorExpiredError` when the sandbox
evicted the oldest log frames of a finished run; they return the output it
kept, as the first page of `process.run()` already did.
