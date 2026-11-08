**TypeScript checks toolbox option keys** — every options object of the
process, session, PTY, interpreter, filesystem, git, LSP and computer-use
calls rejects a key it does not define with `BadRequestError` (400,
`BAD_REQUEST`, source `SDK`) instead of ignoring it, and a renamed key names
its replacement ("`timeout` was renamed to `timeoutSeconds` (seconds)"). New
named option types: `PtyStartOptions`, `WaitProcessOptions`,
`InterpreterContextRunOptions`, `ListFilesOptions`, `MkdirOptions`,
`RemoveOptions`, and the now-exported `InitOptions` and `GitConfigOptions`.
