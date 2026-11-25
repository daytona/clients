**Tag-wide kills and sessions** — `kill_all()` (Python) / `killAll()`
(TypeScript) / `KillAll` (Go) on a tag also ends sessions created with that
tag; it raises `UnsupportedOperationError` on macOS and on sandboxes without
process containment. Only documentation changed.
