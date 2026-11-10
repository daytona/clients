**Python result models hold tuples** — the collection fields of Python's frozen
result models are tuples instead of lists, as list pages and the platform
models already were: `LogPage.items`, `ProcessResult.logs`, `ProcessInfo.argv`,
`BulkWriteResult.files` / `errors`, `BulkReadResult.errors`,
`GitStatus.file_status`, `InterpreterResult.results`,
`InterpreterError.traceback`, the LSP completion `items` and the computer-use
collections (displays, windows, accessibility nodes and their children,
matches, recordings). Nested values can no longer be changed in place, and a
model without a mapping field is hashable. Code that appends to one of these
or concatenates it with a list needs `list(...)` first.
