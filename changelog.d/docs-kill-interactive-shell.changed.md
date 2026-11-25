**Killing an interactive shell** — `kill()` sends `TERM`, which a PTY's
default shell ignores: use `HUP` or `KILL`, or escalate. `kill_all()` on a
tag also ends sessions created with that tag; it raises
`UnsupportedOperationError` on macOS and on sandboxes without process
containment. Only documentation changed.
