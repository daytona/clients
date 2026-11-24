**`start()` of a starting sandbox** — `start()` on a sandbox that another
caller is already starting or resuming now raises
`SandboxTerminalStateError` once the wait's reads confirm that operation
failed and the sandbox returned to `stopped` or `paused` (back at `paused`,
after up to one 30-second long-poll read), instead of waiting out its
timeout and raising `RequestTimeoutError` (Python, TypeScript and Go).
