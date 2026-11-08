**`create*()` and `fork()` return the sandbox** — once the create or fork
request has succeeded, they return the sandbox however the wait after it ends:
the `wait_seconds` / `waitSeconds` budget runs out, a terminal state is
reached, or a read during the wait fails. Check `sandbox.state` and
`sandbox.error`. A raised create or fork means the request itself failed,
except for `SandboxCreateRequestTimeoutError` and
`SandboxCreateConnectionError` (they carry the idempotency key because a
sandbox may exist), caller cancellation, and an unexpected non-SDK error
during the wait. `SandboxCreateTimeoutError` is removed, and a server 408 on
the create request is the server's `RequestTimeoutError` in both SDKs.
