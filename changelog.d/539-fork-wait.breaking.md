**`fork()` waits in seconds for the state its fork settles in** — `fork()`
takes `wait_seconds` / `waitSeconds` (default 60, fractions allowed, `0`
returns once the fork is accepted) instead of `timeout`, and its detail read
counts against that budget. A fork of a running Linux sandbox waits for
`started`; otherwise it rests, `paused` for a memory fork of a paused source
and `stopped` for the rest. A macOS fork settles at `started` or `stopped`.
TypeScript checks `name` and `memory` before any request, as Python does.
