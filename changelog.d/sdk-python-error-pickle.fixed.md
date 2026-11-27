**Python v2 errors survive pickle and copy** — every `daytona.v2` error keeps
its class and attributes through `pickle`, `copy` and `deepcopy`, so
`SandboxTerminalStateError`, `DockerWaitTimeoutError`, `DockerNotReadyError`,
`SandboxCreateRequestTimeoutError` and `SandboxCreateConnectionError` cross
`multiprocessing` / `concurrent.futures` intact, with the right
`idempotency_key`.
