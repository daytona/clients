**Tag-wide kills and sessions** — `kill_all()` (Python) / `killAll()`
(TypeScript) / `KillAll` (Go) on a tag also ends sessions created with that
tag; on macOS and on sandboxes without process containment it raises
`UnsupportedOperationError` (Python, TypeScript) or returns an error matching
`ErrUnsupportedOperation` (Go). Only documentation changed.
