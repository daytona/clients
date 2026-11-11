**A stopped sandbox answers `SandboxNotRunningError`, not not-found** — calls
into a stopped sandbox (`sandbox.process`, `sandbox.fs`, `sandbox.git` and the
other in-sandbox namespaces) and requests to its preview URLs now get 409
`SANDBOX_NOT_RUNNING` from the API instead of 404 `NOT_FOUND`. Both SDKs raise
it as `SandboxNotRunningError`, a `ConflictError`; code that caught
`NotFoundError` for a stopped sandbox should catch `SandboxNotRunningError`
and start the sandbox. In the CLI, such commands exit with code `6` (conflict)
instead of `5`, with a hint to start the sandbox.
