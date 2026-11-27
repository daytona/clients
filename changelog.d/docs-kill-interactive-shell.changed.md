**Killing an interactive shell** — `kill()` (Go `Kill`) sends `TERM` by
default, which a PTY's default shell ignores: use `HUP` or `KILL`, or
escalate. Only documentation changed.
