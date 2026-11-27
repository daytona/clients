**TypeScript wrapped errors keep their cause** —
`SandboxCreateConnectionError`, `SandboxCreateRequestTimeoutError`,
`DockerWaitTimeoutError`, build-log read failures and a sandbox wait cut at
its deadline keep the error they replace as `cause`, as the Python SDK chains
it. The three constructors take an optional `{ cause }`.
