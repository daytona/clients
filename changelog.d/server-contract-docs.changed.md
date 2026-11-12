**Documented server rules** — both SDKs and the CLI describe the platform's
current behavior: process logs read with `encoding="base64"` are standard
padded base64 (the standard library decodes them); PTY sizes are 1 to 65535
columns and rows, and a Windows sandbox refuses more than 32767; a port's
visibility change takes effect immediately on event delivery, at most 30 s if
an event is missed; image names may not start with `img_`, `snap_`, `sbx_`,
`vol_` or `fn_` (`ImageNameReservedError`), and a named import whose name exists
for different content raises `ImageNameTakenError` while an identical one
returns the existing image; an unknown interpreter language raises
`UnsupportedOperationError` (400), while `KernelUnavailableError` (503) means a
supported language's kernel failed to start. Git failures raise
`GitAuthFailedError`, `GitRepoNotFoundError`, `GitBranchNotFoundError`,
`GitBranchExistsError` or `ConflictError`.
