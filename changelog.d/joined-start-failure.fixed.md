**`start()` of a starting sandbox** — `start()` on a sandbox that another
caller is already starting or resuming now raises
`SandboxTerminalStateError` when that operation fails and the sandbox
returns to `stopped` or `paused`, once the wait's long-poll reads confirm it
(up to one 30-second slice), instead of waiting out its timeout and raising
`RequestTimeoutError` (Python, TypeScript and Go).
