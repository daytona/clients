**CLI: shell-style exit codes for signals** — a sandbox process ended by a
signal now exits `128` + the signal's number (`SIGKILL` → `137`, `SIGTERM` →
`143`; `SIGINT` stays `130`) instead of `1`, and the CLI itself exits `143`
when it receives SIGTERM (it exited `130`). A missing local source of
`sandbox cp`, or a directory without `-r`, is a usage error (exit 2) before
anything is signed or sent.
