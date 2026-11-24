**`start()` of a starting sandbox** — `start()` on a sandbox that another
caller is already starting or resuming now raises
`SandboxTerminalStateError` as soon as that operation fails and the sandbox
returns to `stopped` or `paused`, instead of waiting out its timeout (Python,
TypeScript and Go).
