**Errors keep their details** — Python errors survive `pickle` and
`copy` with their class and every attribute, so `SandboxTerminalStateError`,
`DockerWaitTimeoutError`, `DockerNotReadyError`, `SandboxCreateRequestTimeoutError`
and `SandboxCreateConnectionError` cross `multiprocessing` /
`concurrent.futures` intact, with the right `idempotency_key`. An error
response's message is never empty in any SDK: an empty or blank body, or an
envelope whose `message` is empty, reads `HTTP <status>` (a non-empty `error`
still wins), and a JSON body that is not an object, such as `null`, is its
text. TypeScript's `ConnectionError` keeps the runtime's network error
(`ECONNREFUSED`, `ENOTFOUND`, ...) as its `cause`, never the HTTP client's
error with its request headers, and `SandboxCreateConnectionError`,
`SandboxCreateRequestTimeoutError`, `DockerWaitTimeoutError` and a sandbox
wait that ran out of time on a read keep the error they replace as their
`cause`, as Python chains it.
