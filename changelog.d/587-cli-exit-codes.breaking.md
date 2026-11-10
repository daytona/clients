**CLI: shell-style exit codes for signals** — a sandbox process ended by a
signal now exits `128` + the signal's number when it is the same on Linux and
macOS (`SIGKILL` → `137`, `SIGTERM` → `143`; `SIGINT` stays `130`; any other
signal `1`) instead of `1`, and the CLI itself exits `143`
when it receives SIGTERM (it exited `130`), `sandbox ssh` included (it
exited `255` when interrupted).
