**`fork()` waits in seconds for the state its fork settles in** — `fork()`
takes `wait_seconds` / `waitSeconds` (default 60, fractions allowed) instead
of `timeout`, and its detail read counts against that budget. `0` is one
request, as for `create`: the fork returns once accepted, with what the fork
response carries and no detail read, and the source's class only when the
source handle already knows it (no class read first). A fork of a running
Linux sandbox waits for `started`; otherwise it rests, `paused` for a memory
fork of a paused source and `stopped` for the rest. A macOS fork settles at
`started` or `stopped`. TypeScript checks `name` and `memory` before any
request, as Python does.
