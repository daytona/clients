**TypeScript `Sandbox.state` is always set** — the getter is typed
`SandboxState` instead of `SandboxState | undefined`: it reads `'unknown'`
before the state is known, as in Python.
