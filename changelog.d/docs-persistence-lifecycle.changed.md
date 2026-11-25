**Persistent sandboxes** — `stop`, `pause`, `resume` and `fork` need a
persistent sandbox (fork also refuses attached volumes); the Python,
TypeScript and Go docs and the CLI help now say so, and the `persistent`
property (Go `Info().Persistent`) is no longer described as macOS-only. Only
documentation and CLI help changed.
