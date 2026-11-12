**`create*()` and `fork()` return the sandbox** — once the create or fork
request has succeeded, they return the sandbox however the wait after it ends:
the `wait_seconds` / `waitSeconds` budget runs out, a terminal state is
reached, or a read during the wait fails (any `DaytonaError`, a response that
breaks the contract included). Check `sandbox.state` and `sandbox.error`. A
raised create or fork means the request itself failed, with these exceptions:
a 2xx response without a usable `id` (`BadRequestError`; the sandbox may
exist); `SandboxCreateRequestTimeoutError` and `SandboxCreateConnectionError`,
raised when the create request timed out or lost its connection, which carry
the idempotency key to retry with because a sandbox may exist; caller
cancellation; and an unexpected non-SDK error during the wait.
